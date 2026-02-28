import json
import logging
import re
from typing import Any, Dict, List
from server.agents.base import BaseAgent, AgentResult, PipelineContext, PipelineStage, registry
from server.config import settings
from google import genai
from pydantic import BaseModel, Field

class ReferenceModel(BaseModel):
    authors: str = Field(description="Author names (e.g., 'Smith et al.')")
    title: str | None = Field(default=None, description="Paper title if mentioned")
    year: int = Field(description="Publication year")
    venue: str | None = Field(default=None, description="Conference/journal if mentioned")
    arxiv_id: str | None = Field(default=None, description="ArXiv identifier if present (e.g., '2411.04710' or 'cs/0610105')")
    doi: str | None = Field(default=None, description="DOI if present (e.g., '10.1145/127135.127136')")

class CitationModel(BaseModel):
    id: int = Field(description="Sequential identifier")
    claim: str = Field(description="What the paper claims about this citation.")
    context: str | None = Field(default=None, description="Original sentence containing citation")
    reference: ReferenceModel

class ExtractorAgent(BaseAgent):
    name = "extractor"
    stage = PipelineStage.EXTRACTING
    description = "Extract citations using LLM"
    requires_tokens = True

    def __init__(self, model_name=None):
        super().__init__()
        try:
            self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        except Exception as e:
            logging.error(f"Failed to init GenAI client: {e}")
            self.client = None
        self.model_name = model_name or settings.LLM_MODEL

    def _preprocess_text(self, text: str, max_chars: int = 200_000) -> str:
        """Use full document up to end of References (or before Appendix). Never cut the References section."""
        if not text:
            return ""
        # 1) Cut at Appendix so we don't feed appendix
        appendix_match = re.search(r"\n\s*Append(?:ix|ices)(?:\s|$|\d)", text, re.IGNORECASE)
        if appendix_match:
            text = text[: appendix_match.start()].rstrip()
        # 2) Find References/Bibliography section start
        refs_match = re.search(r"\n\s*(References|Bibliography|Reference List|Works Cited)\s*\n", text, re.IGNORECASE)
        refs_start = refs_match.start() if refs_match else None
        # 3) If under limit, return as-is
        if len(text) <= max_chars:
            return text
        # 4) Over limit: keep start + tail so tail includes entire References section
        tail_len = int(max_chars * 0.55)
        head_len = max_chars - tail_len - 60
        if refs_start is not None and refs_start < len(text) - tail_len:
            tail_start = min(refs_start, len(text) - tail_len)
            tail_start = max(0, tail_start)
            return text[:head_len] + "\n\n[... truncated ...]\n\n" + text[tail_start:]
        return text[:head_len] + "\n\n[... truncated ...]\n\n" + text[-tail_len:]

    def _extract_references_section(self, text: str) -> str | None:
        """Extract only the References/Bibliography section so the LLM can list every entry without output truncation."""
        if not text:
            return None
        refs_match = re.search(
            r"\n\s*(References|Bibliography|Reference List|Works Cited)\s*\n",
            text,
            re.IGNORECASE,
        )
        if refs_match:
            return text[refs_match.start() :].strip()
        return None

    def _normalize_raw_citation(self, c: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize LLM output (e.g. authors list → string)."""
        c = dict(c)
        ref = c.get("reference") or {}
        if isinstance(ref, dict):
            ref = dict(ref)
            authors = ref.get("authors")
            if isinstance(authors, list):
                ref["authors"] = ", ".join(str(a) for a in authors) if authors else ""
            elif authors is None:
                ref["authors"] = ""
            year = ref.get("year")
            if year is None or (isinstance(year, (str, float)) and not year):
                ref["year"] = 0
            elif not isinstance(year, int):
                try:
                    ref["year"] = int(year)
                except (TypeError, ValueError):
                    ref["year"] = 0
            c["reference"] = ref
        return c

    def _validate_extraction(self, citations_from_llm: List[CitationModel]) -> List[Dict]:
        valid = []
        seen_refs = set()
        for i, c in enumerate(citations_from_llm):
            if not c.claim or len(c.claim) < 5:
                continue
            if not (c.reference.authors or c.reference.title or (c.reference.year and c.reference.year != 0)):
                continue
            if c.reference.title:
                ref_key = c.reference.title.lower().strip()
            elif c.reference.authors and c.reference.year:
                ref_key = f"{c.reference.authors}_{c.reference.year}".lower().strip()
            elif c.reference.authors:
                ref_key = c.reference.authors.lower().strip()
            else:
                ref_key = str(i)
                
            if ref_key in seen_refs:
                continue
            seen_refs.add(ref_key)

            valid.append({
                "id": len(valid) + 1,
                "claim": c.claim.strip(),
                "context": c.context,
                "reference": {
                    "authors": c.reference.authors,
                    "title": c.reference.title,
                    "year": c.reference.year,
                    "venue": c.reference.venue,
                    "arxiv_id": c.reference.arxiv_id,
                    "doi": c.reference.doi
                }
            })
        return valid

    async def process(self, ctx: PipelineContext) -> AgentResult:
        if not self.client:
            return AgentResult(agent_name=self.name, status="error", error="GenAI client missing. GEMINI_API_KEY needed.")
            
        text = ctx.paper_text
        if not text:
            return AgentResult(agent_name=self.name, status="error", error="No paper text available for extraction")

        processed_text = self._preprocess_text(text)
        # Prefer References section only so the LLM lists every entry (avoids output truncation)
        refs_section = self._extract_references_section(processed_text)
        content_for_extraction = refs_section if refs_section else processed_text
        if refs_section:
            instruction = (
                "Below is ONLY the References/Bibliography section of a paper. "
                "List EVERY reference as a JSON array. One object per reference. Do not skip any. "
                "Each object: id (1,2,3,...), claim (use 'Cited in paper' or a one-line summary from the title), "
                "context (null), reference: {authors, title, year, venue, arxiv_id, doi}. "
                "Return ONLY a valid JSON array. No markdown."
            )
        else:
            instruction = (
                "Extract EVERY reference from the References/Bibliography section. "
                "Output ONE JSON object per reference. Do not skip any. "
                "Each object: id, claim, context, reference: {authors, title, year, venue, arxiv_id, doi}. "
                "Return ONLY a valid JSON array. No markdown."
            )

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            use_gemma = "gemma" in (self.model_name or "").lower()

            def run_genai():
                if use_gemma:
                    json_spec = "JSON array only. Each element: id, claim, context, reference (authors, title, year, venue, arxiv_id, doi)."
                    final_contents = f"{instruction}\n{json_spec}\n\nText:\n{content_for_extraction}"
                    config_dict = {"temperature": 0.1}
                else:
                    final_contents = content_for_extraction
                    config_dict = {
                        "system_instruction": instruction,
                        "response_mime_type": "application/json",
                        "response_schema": list[CitationModel],
                        "temperature": 0.0,
                    }
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=final_contents,
                    config=config_dict,
                )

            response = await loop.run_in_executor(None, run_genai)

            if use_gemma:
                raw = (response.text or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```", 2)[1]
                    if raw.lower().startswith("json"):
                        raw = raw[4:].lstrip()
                citations_raw = json.loads(raw)
                citations = [CitationModel(**self._normalize_raw_citation(c)) for c in citations_raw]
            elif hasattr(response, "parsed") and response.parsed:
                citations = response.parsed
            else:
                raw = (response.text or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```", 2)[1]
                    if raw.lower().startswith("json"):
                        raw = raw[4:].lstrip()
                citations_raw = json.loads(raw)
                citations = [CitationModel(**self._normalize_raw_citation(c)) for c in citations_raw]

            valid_citations = self._validate_extraction(citations)

            tokens_used = 0
            if response.usage_metadata:
                p = response.usage_metadata.prompt_token_count
                c = response.usage_metadata.candidates_token_count
                tokens_used = (p or 0) + (c or 0)
                
            return AgentResult(
                agent_name=self.name,
                status="success",
                data={"citations": valid_citations},
                tokens_used=tokens_used,
            )
        except Exception as e:
            logging.error(f"LLM extraction failed: {e}")
            raise Exception("EXTRACT_FAILED: " + str(e))

registry.register(ExtractorAgent())
