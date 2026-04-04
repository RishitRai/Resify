import json
import logging
import re
from typing import Dict, List
from server.agents.base import BaseAgent, AgentResult, PipelineContext, PipelineStage, registry
from google import genai
from pydantic import BaseModel, Field


class ReferenceModel(BaseModel):
    authors: str = Field(description="Author names (e.g., 'Smith et al.')")
    title: str = Field(description="Full title of the cited paper.")
    year: int = Field(description="Publication year")
    venue: str | None = Field(default=None, description="Conference/journal if mentioned")


class CitationModel(BaseModel):
    id: int = Field(description="The reference number from the paper (e.g., 1 for [1])")
    claim: str = Field(description="What THIS paper says ABOUT the cited work — the reason it is cited.")
    context: str | None = Field(default=None, description="Original sentence containing the citation")
    reference: ReferenceModel


class ExtractorAgent(BaseAgent):
    name = "extractor"
    stage = PipelineStage.EXTRACTING
    description = "Extract citations using LLM"
    requires_tokens = True

    def __init__(self, model_name="gemini-2.5-flash"):
        super().__init__()
        try:
            from server.config import settings
            self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        except Exception as e:
            logging.error(f"Failed to init GenAI client: {e}")
            self.client = None
        self.model_name = model_name

    def _preprocess_text(self, text: str, max_chars: int = 120000) -> str:
        if not text:
            return ""
        if len(text) <= max_chars:
            return text

        lower = text.lower()
        refs_idx = lower.rfind('\nreferences\n')
        if refs_idx < 0:
            refs_idx = lower.rfind('\nreferences')
        if refs_idx < 0:
            refs_idx = lower.rfind('references\n')

        if refs_idx > 0:
            refs_section = text[refs_idx:]
            body_budget = max_chars - len(refs_section)
            if body_budget > 10000:
                return text[:body_budget] + "\n\n[...body truncated...]\n\n" + refs_section
            half = max_chars // 2
            return text[:half] + "\n\n[...truncated...]\n\n" + text[-half:]

        return text[:max_chars]

    def _parse_references_section(self, text: str) -> List[Dict]:
        """Parse reference entries from the bibliography section.

        Handles:
          - Numbered: [1], [2], ...
          - Tagged: [ABC20], [ACG+16], ...
        """
        lower = text.lower()
        refs_idx = lower.rfind('\nreferences\n')
        if refs_idx < 0:
            refs_idx = lower.rfind('\nreferences')
        if refs_idx < 0:
            return []

        refs_text = text[refs_idx:]

        # Try numbered [N] first
        numbered_parts = re.split(r'\n\[(\d+)\]\s*', refs_text)
        if len(numbered_parts) >= 3:
            parsed = []
            i = 1
            while i + 1 < len(numbered_parts):
                ref_num = int(numbered_parts[i])
                ref_body = numbered_parts[i + 1].strip()
                ref_body = re.sub(r'\s*\n\s*', ' ', ref_body)
                parsed.append({"num": ref_num, "raw": ref_body})
                i += 2
            return parsed

        # Try tagged [TAG] style (e.g. [ACG+16], [AAAK20])
        tagged_parts = re.split(r'\n\[([A-Za-z+]+\d{2,4}[a-z]?)\]\s*', refs_text)
        if len(tagged_parts) >= 3:
            parsed = []
            i = 1
            num = 1
            while i + 1 < len(tagged_parts):
                tag = tagged_parts[i]
                ref_body = tagged_parts[i + 1].strip()
                ref_body = re.sub(r'\s*\n\s*', ' ', ref_body)
                parsed.append({"num": num, "tag": tag, "raw": ref_body})
                num += 1
                i += 2
            return parsed

        return []

    def _extract_ref_metadata(self, raw: str) -> Dict:
        """Extract author, title, year from a raw reference string."""
        # Year: find 4-digit year
        year_match = re.search(r'\b(19|20)\d{2}\b', raw)
        year = int(year_match.group()) if year_match else 0

        # Try to split author from title
        # Common patterns: "Author1, Author2, and Author3. Year. Title."
        # or "Author1, Author2. Title. Venue, Year."
        authors = ""
        title = ""

        # Pattern 1: "Authors. Year. Title. Venue..."
        m = re.match(r'^(.+?)\.\s*(?:19|20)\d{2}[a-z]?\.\s*(.+?)\.', raw)
        if m:
            authors = m.group(1).strip()
            title = m.group(2).strip()
        else:
            # Pattern 2: "Authors. Title. In Proceedings..."
            m = re.match(r'^(.+?)\.\s+(.+?)(?:\.\s+(?:In |Advances |Proceedings|arXiv|CoRR|http))', raw)
            if m:
                authors = m.group(1).strip()
                title = m.group(2).strip()
            else:
                # Fallback: first sentence-ish chunk is authors, second is title
                sentences = re.split(r'\.\s+', raw, maxsplit=2)
                if len(sentences) >= 2:
                    authors = sentences[0].strip()
                    title = sentences[1].strip()
                else:
                    authors = raw[:60]
                    title = raw

        # Clean up
        title = re.sub(r'\s+', ' ', title).strip().rstrip('.')
        authors = re.sub(r'\s+', ' ', authors).strip()

        return {"authors": authors, "title": title, "year": year}

    def _validate_extraction(self, citations_from_llm: List[CitationModel]) -> List[Dict]:
        valid = []
        seen_refs = set()
        for i, c in enumerate(citations_from_llm):
            if not c.claim or len(c.claim) < 10:
                continue
            if not c.reference.authors and not c.reference.year:
                continue

            if c.reference.title:
                ref_key = c.reference.title.lower().strip()[:80]
            elif c.reference.authors and c.reference.year:
                ref_key = f"{c.reference.authors}_{c.reference.year}".lower().strip()
            else:
                ref_key = str(i)

            if ref_key in seen_refs:
                continue
            seen_refs.add(ref_key)

            valid.append({
                "id": c.id,
                "claim": c.claim.strip(),
                "context": c.context,
                "reference": {
                    "authors": c.reference.authors,
                    "title": c.reference.title,
                    "year": c.reference.year,
                    "venue": c.reference.venue
                }
            })
        return valid

    def _merge_regex_and_llm(self, regex_refs: List[Dict], llm_citations: List[Dict]) -> List[Dict]:
        """Ensure every reference from the bibliography appears in the output.

        LLM citations have rich claims. Regex refs have accurate metadata.
        Merge: prefer LLM data when it exists, fill gaps from regex.
        """
        # Index LLM citations by their id (reference number)
        llm_by_id = {c["id"]: c for c in llm_citations}

        merged = []
        for ref in regex_refs:
            num = ref["num"]
            meta = ref["meta"]

            if num in llm_by_id:
                llm_cit = llm_by_id[num]
                # Use LLM claim/context, but prefer regex title if LLM title looks generic
                entry = {
                    "id": num,
                    "claim": llm_cit["claim"],
                    "context": llm_cit.get("context"),
                    "reference": {
                        "authors": llm_cit["reference"].get("authors") or meta["authors"],
                        "title": llm_cit["reference"].get("title") or meta["title"],
                        "year": llm_cit["reference"].get("year") or meta["year"],
                        "venue": llm_cit["reference"].get("venue"),
                    }
                }
            else:
                # LLM missed this reference — use regex metadata with generic claim
                entry = {
                    "id": num,
                    "claim": f"Referenced as [{num}] in the paper.",
                    "context": None,
                    "reference": {
                        "authors": meta["authors"],
                        "title": meta["title"],
                        "year": meta["year"],
                        "venue": None,
                    }
                }
            merged.append(entry)

        # Renumber sequentially
        merged.sort(key=lambda c: c["id"])
        for i, c in enumerate(merged):
            c["id"] = i + 1

        return merged

    async def process(self, ctx: PipelineContext) -> AgentResult:
        if not self.client:
            return AgentResult(agent_name=self.name, status="error", error="GenAI client missing. GEMINI_API_KEY needed.")

        text = ctx.paper_text
        if not text:
            return AgentResult(agent_name=self.name, status="error", error="No paper text available for extraction")

        # Step 1: Parse references section with regex to get all [N] entries
        regex_refs = self._parse_references_section(text)
        for ref in regex_refs:
            ref["meta"] = self._extract_ref_metadata(ref["raw"])

        # Step 2: Send to LLM for claim extraction
        processed_text = self._preprocess_text(text)
        ref_count = len(regex_refs)

        if ref_count > 0:
            if "tag" in regex_refs[0]:
                ref_list = ", ".join(f'[{r["tag"]}]' for r in regex_refs[:10])
                style_hint = f"tagged like {ref_list}, etc."
            else:
                style_hint = f"numbered [1] through [{ref_count}]."

            system_instruction = f'''You are an expert academic research assistant extracting citations from an academic paper.
This paper has {ref_count} references {style_hint}

INSTRUCTIONS:
1. For EVERY reference in the bibliography, find where it is cited in the paper body and extract what the paper claims about it.
2. The "id" field must be sequential: 1, 2, 3, ... {ref_count} — matching the order they appear in the references section.
3. The "claim" must describe what THIS paper says about the cited work — why it cites it.
4. Get the full title from the references section at the end.
5. You MUST return exactly {ref_count} entries, one per reference.
6. Return a JSON array.'''
        else:
            system_instruction = '''You are an expert academic research assistant extracting citations from an academic paper.

INSTRUCTIONS:
1. Extract EVERY unique cited work from this paper. Check the references/bibliography section for the full list.
2. The "id" field must be sequential starting from 1.
3. The "claim" must describe what THIS paper says about the cited work — why it cites it.
4. Get the full title from the references/bibliography section.
5. Return a JSON array.'''

        try:
            import asyncio
            loop = asyncio.get_event_loop()

            def run_genai():
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=processed_text,
                    config={
                        "system_instruction": system_instruction,
                        "response_mime_type": "application/json",
                        "response_schema": list[CitationModel],
                        "temperature": 0.1,
                    }
                )

            response = await loop.run_in_executor(None, run_genai)

            tokens_used = 0
            if response.usage_metadata:
                tokens_used = response.usage_metadata.prompt_token_count + response.usage_metadata.candidates_token_count

            # Parse LLM response — try multiple strategies
            llm_citations: List[Dict] = []
            try:
                if hasattr(response, "parsed") and response.parsed:
                    llm_citations = self._validate_extraction(response.parsed)
                else:
                    citations_raw = json.loads(response.text)
                    citations = [CitationModel(**c) for c in citations_raw]
                    llm_citations = self._validate_extraction(citations)
            except (json.JSONDecodeError, Exception) as parse_err:
                logging.warning(f"LLM JSON parse failed: {parse_err}, attempting repair")
                # Try to salvage partial JSON
                try:
                    raw = response.text
                    # Fix common issues: trailing commas, truncated output
                    raw = re.sub(r',\s*([}\]])', r'\1', raw)
                    # Try to close unclosed array
                    if raw.count('[') > raw.count(']'):
                        raw = raw.rstrip().rstrip(',') + ']'
                    citations_raw = json.loads(raw)
                    citations = [CitationModel(**c) for c in citations_raw]
                    llm_citations = self._validate_extraction(citations)
                    logging.info(f"JSON repair recovered {len(llm_citations)} citations")
                except Exception:
                    logging.warning("JSON repair failed, using regex-only references")

            # Step 3: Merge — guarantees every bibliography entry is present
            if regex_refs:
                final_citations = self._merge_regex_and_llm(regex_refs, llm_citations)
            elif llm_citations:
                final_citations = llm_citations
            else:
                return AgentResult(agent_name=self.name, status="error", error="Could not parse any citations")

            return AgentResult(
                agent_name=self.name,
                status="success",
                data={"citations": final_citations},
                tokens_used=tokens_used,
            )
        except Exception as e:
            # If we have regex refs, return those rather than failing entirely
            if regex_refs:
                logging.error(f"LLM extraction failed: {e}, falling back to regex-only")
                for ref in regex_refs:
                    if "meta" not in ref:
                        ref["meta"] = self._extract_ref_metadata(ref["raw"])
                fallback = self._merge_regex_and_llm(regex_refs, [])
                return AgentResult(
                    agent_name=self.name,
                    status="success",
                    data={"citations": fallback},
                    tokens_used=0,
                )
            logging.error(f"LLM extraction failed: {e}")
            raise Exception("EXTRACT_FAILED: " + str(e))

registry.register(ExtractorAgent())
