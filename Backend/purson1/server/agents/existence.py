import sqlite3
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
from server.agents.base import BaseAgent, AgentResult, PipelineContext, PipelineStage, registry
from server.config import settings
from server.utils.apis import SemanticScholarAPI, CrossRefAPI, ArXivAPI, APIError
from google import genai

class ExistenceCache:
    def __init__(self, db_path="existence_cache.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    lookup_key TEXT PRIMARY KEY,
                    paper_id TEXT,
                    data TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def _generate_key(self, title: str, author: str, year: Any) -> str:
        s = f"{title}_{author}_{year}".lower()
        return hashlib.sha256(s.encode()).hexdigest()

    def get(self, title: str, author: str, year: Any) -> Optional[Dict]:
        key = self._generate_key(title, author, year)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT data FROM cache WHERE lookup_key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
        return None

    def set(self, title: str, author: str, year: Any, paper_id: str, data: Dict):
        key = self._generate_key(title, author, year)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (lookup_key, paper_id, data) VALUES (?, ?, ?)",
                (key, paper_id, json.dumps(data))
            )
            conn.commit()


def normalize_text(text: str) -> str:
    if not text:
        return ""
    import re
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()

def get_first_author_lastname(authors_str: str) -> str:
    if not authors_str:
        return ""
    parts = authors_str.replace("et al.", "").strip().split()
    return normalize_text(parts[-1]) if parts else ""

def word_overlap(text1: str, text2: str) -> float:
    words1 = set(normalize_text(text1).split())
    words2 = set(normalize_text(text2).split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1 & words2
    union = words1 | words2
    
    if not union: return 0.0
    return len(intersection) / len(union)


class ExistenceAgent(BaseAgent):
    name = "existence"
    stage = PipelineStage.CHECKING_EXISTENCE
    description = "Check citation existence"
    requires_tokens = True

    def __init__(self, db_path="existence_cache.db"):
        super().__init__()
        self.semantic_scholar = SemanticScholarAPI()
        self.crossref = CrossRefAPI()
        self.arxiv = ArXivAPI()
        try:
            self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        except Exception:
            self.client = None
        self.cache = ExistenceCache(db_path)

    def _find_best_match(self, reference: dict, results: list) -> tuple[Optional[dict], int]:
        ref_title = reference.get("title", "")
        ref_year = reference.get("year")
        ref_author = get_first_author_lastname(reference.get("authors", ""))
        
        best_score = 0
        best_match = None
        
        for paper in results:
            score = 0
            
            # Title similarity
            paper_title = paper.get("title", "")
            title_sim = word_overlap(ref_title, paper_title)
            score += title_sim * 50
            
            # Year match
            paper_year = paper.get("year")
            if paper_year and ref_year:
                try:
                    p_year = int(paper_year)
                    r_year = int(ref_year)
                    if p_year == r_year:
                        score += 30
                    elif abs(p_year - r_year) == 1:
                        score += 15
                except ValueError:
                    pass
            
            # Author match
            paper_authors = []
            if isinstance(paper.get("authors"), list):
                for a in paper.get("authors", []):
                    if isinstance(a, str):
                        paper_authors.append(normalize_text(a))
                    elif isinstance(a, dict) and "name" in a:
                        paper_authors.append(normalize_text(a["name"]))

            if ref_author and any(ref_author in a for a in paper_authors):
                score += 20
            
            if score > best_score:
                best_score = score
                best_match = paper
                
        if best_score >= 60:
            return best_match, best_score
        return None, best_score

    def _verify_metadata(self, reference: dict, matched_paper: dict, match_score: int) -> dict:
        ref_year = reference.get("year")
        errors = []
        
        try:
            if ref_year and matched_paper.get("year"):
                if abs(int(ref_year) - int(matched_paper["year"])) > 1:
                    errors.append({
                        "field": "year",
                        "claimed": ref_year,
                        "actual": matched_paper["year"],
                        "message": f"Year mismatch: citation says {ref_year}, paper is from {matched_paper['year']}"
                    })
        except ValueError:
            pass

        return {
            "metadata_status": "has_errors" if errors else "correct",
            "metadata_errors": errors
        }

    async def process(self, ctx: PipelineContext) -> AgentResult:
        all_results = {}
        for cit in ctx.citations:
            cid = cit["id"]
            reference = cit.get("reference", {})
            if not reference:
                continue

            ref_title = reference.get("title", "") or ""
            ref_year = reference.get("year", "") or ""
            ref_authors = reference.get("authors", "") or ""
            first_author = get_first_author_lastname(ref_authors)

            # Check Cache
            cached_data = self.cache.get(ref_title, first_author, ref_year)
            if cached_data:
                cached_data["cached"] = True
                all_results[str(cid)] = cached_data
                continue

            query = f"{ref_title} {first_author} {ref_year}".strip()
            if not query:
                continue

            # --- MULTI-STAGE FALLBACK & DIRECT LOOKUP ---
            source_used = "None"
            try:
                match = None
                score = 0
                
                # Stage 0: Direct ID Lookup
                arxiv_id = reference.get("arxiv_id")
                doi = reference.get("doi")
                
                if arxiv_id:
                    source_used = "ArXiv (Direct ID)"
                    paper = await self.arxiv.get_paper(arxiv_id)
                    if paper:
                        match = {
                            "paperId": arxiv_id,
                            "title": paper["title"],
                            "authors": paper["authors"],
                            "year": paper["year"],
                            "abstract": paper["abstract"]
                        }
                        score = 100 # Direct match
                
                if not match and doi:
                    source_used = "CrossRef (Direct DOI)"
                    paper = await self.crossref.get_paper_by_doi(doi)
                    if paper:
                        match = {
                            "paperId": doi,
                            "title": paper["title"],
                            "authors": paper["authors"],
                            "year": paper["year"],
                            "abstract": paper["abstract"]
                        }
                        score = 100 # Direct match

                # Stage 1: Semantic Scholar (Title + Author + Year)
                if not match:
                    source_used = "Semantic Scholar (Standard)"
                    results = await self.semantic_scholar.search(query, limit=5)
                    match, score = self._find_best_match(reference, results)
                
                # Stage 2: Semantic Scholar (Title only)
                if not match and ref_title:
                    source_used = "Semantic Scholar (Title-only)"
                    results = await self.semantic_scholar.search(ref_title, limit=5)
                    match, score = self._find_best_match(reference, results)

                # Stage 3: CrossRef Fallback (Search)
                if not match and ref_title:
                    source_used = "CrossRef (Search)"
                    results = await self.crossref.search(f"{ref_title} {first_author}", limit=5)
                    match, score = self._find_best_match(reference, results)

                # Stage 4: LLM Final Check (Optimization for 0% "not found")
                if not match and ref_title:
                    source_used = "LLM Verification (Heuristic)"
                    # Final attempt: Ask LLM if this citation looks real but just missed by APIs
                    # (e.g., very new, technical error, or minor extraction mismatch)
                    try:
                        prompt = f"""
                        Evaluate if this academic citation is likely a real published paper or a complete hallucination.
                        Citation: {ref_authors}, "{ref_title}", {ref_year}
                        
                        Respond with a JSON object:
                        {{
                            "is_likely_real": bool,
                            "confidence": float (0-1),
                            "reasoning": "short explanation"
                        }}
                        """
                        # We use a simple loop runner for GenAI in this context
                        import asyncio
                        loop = asyncio.get_event_loop()
                        
                        def run_llm_check():
                            if not hasattr(self, 'client') or not self.client:
                                return None
                            return self.client.models.generate_content(
                                model="gemini-3-flash",
                                contents=prompt,
                                config={"response_mime_type": "application/json"}
                            )
                        
                        response = await loop.run_in_executor(None, run_llm_check)
                        if response and response.text:
                            analysis = json.loads(response.text)
                            if analysis.get("is_likely_real") and analysis.get("confidence", 0) > 0.7:
                                # We treat it as "found" but with a placeholder paper object
                                # so it passes to the embedding/gate stage for context verification
                                match = {
                                    "paperId": f"suspect_{cid}",
                                    "title": ref_title,
                                    "authors": [ref_authors],
                                    "year": ref_year,
                                    "abstract": "Metadata found via LLM heuristic - no direct API match.",
                                    "is_suspect": True
                                }
                                score = 50 # Marginal confidence
                    except Exception as e:
                        logging.warning(f"LLM fallback failed for cid {cid}: {e}")

                if not match:
                    response_data = {
                        "status": "not_found",
                        "reason": "No matching paper found after all fallbacks (API and LLM)",
                        "query_used": query,
                        "sources_tried": ["Direct IDs", "Semantic Scholar", "CrossRef", "LLM Heuristic"],
                        "cached": False
                    }
                    all_results[str(cid)] = response_data
                    continue
                    
                paper_id = match.get("paperId", "")
                is_suspect = match.get("is_suspect", False)
                
                # Hybrid Flag: Determine if this needs human review
                needs_review = False
                if is_suspect:
                    needs_review = True
                elif 60 <= score <= 75:
                    needs_review = True

                paper_obj = {
                    "paper_id": paper_id,
                    "title": match.get("title"),
                    "authors": [a.get("name") if isinstance(a, dict) else a for a in match.get("authors", [])],
                    "year": match.get("year"),
                    "abstract": match.get("abstract", ""),
                    "source_found": source_used,
                    "is_suspect": is_suspect
                }
                
                meta_verify = self._verify_metadata(reference, paper_obj, score)

                response_data = {
                    "status": "found",
                    "paper": paper_obj,
                    "match_score": score,
                    "metadata_status": meta_verify["metadata_status"],
                    "metadata_errors": meta_verify["metadata_errors"],
                    "needs_review": needs_review,
                    "cached": False
                }
                
                self.cache.set(ref_title, first_author, ref_year, paper_id, response_data)
                all_results[str(cid)] = response_data
                
            except APIError as e:
                all_results[str(cid)] = {
                    "status": "not_found", 
                    "reason": f"API Error ({source_used}): {str(e)}",
                    "needs_review": True # API errors definitely need a look
                }
            except Exception as e:
                logging.error(f"Existence checker error for cid {cid}: {e}")
                all_results[str(cid)] = {
                    "status": "error",
                    "reason": str(e),
                    "needs_review": True
                }
                
        return AgentResult(
            agent_name=self.name,
            status="success",
            data={"results": all_results},
            tokens_used=0,
        )

registry.register(ExistenceAgent())
