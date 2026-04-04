import asyncio
import aiosqlite
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
from server.agents.base import BaseAgent, AgentResult, PipelineContext, PipelineStage, registry
from server.utils.apis import SemanticScholarAPI, CrossRefAPI, APIError

class ExistenceCache:
    def __init__(self, db_path="existence_cache.db"):
        self.db_path = db_path
        
    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    lookup_key TEXT PRIMARY KEY,
                    paper_id TEXT,
                    data TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

    def _generate_key(self, title: str, author: str, year: Any) -> str:
        s = f"{title}_{author}_{year}".lower()
        return hashlib.sha256(s.encode()).hexdigest()

    async def get(self, title: str, author: str, year: Any) -> Optional[Dict]:
        key = self._generate_key(title, author, year)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT data FROM cache WHERE lookup_key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return json.loads(row[0])
        return None

    async def set(self, title: str, author: str, year: Any, paper_id: str, data: Dict):
        key = self._generate_key(title, author, year)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO cache (lookup_key, paper_id, data) VALUES (?, ?, ?)",
                (key, paper_id, json.dumps(data))
            )
            await db.commit()

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
    requires_tokens = False

    def __init__(self, db_path="existence_cache.db", concurrency_limit=2):
        super().__init__()
        from server.config import settings
        self.semantic_scholar = SemanticScholarAPI(api_key=settings.S2_API_KEY or None)
        self.crossref = CrossRefAPI()
        self.cache = ExistenceCache(db_path)
        self.concurrency_limit = concurrency_limit

    def _find_best_match(self, reference: dict, results: list) -> tuple[Optional[dict], int]:
        ref_title = reference.get("title", "") or ""
        ref_year = reference.get("year")
        ref_author = get_first_author_lastname(reference.get("authors", ""))
        has_title = bool(ref_title.strip())

        best_score = 0
        best_match = None

        for paper in results:
            score = 0

            # Title similarity
            paper_title = paper.get("title", "")
            if has_title and paper_title:
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

        # When no title was provided, author+year (50) is strong enough
        threshold = 60 if has_title else 45
        if best_score >= threshold:
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

    async def _process_single_citation(self, cit: dict, semaphore: asyncio.Semaphore) -> tuple[int, dict]:
        """A bounded asynchronous worker for processing precisely one citation."""
        async with semaphore:
            cid = cit["id"]
            reference = cit.get("reference", {})
            if not reference:
                return cid, {"status": "error", "reason": "No reference data"}

            ref_title = reference.get("title", "") or ""
            ref_year = reference.get("year", "") or ""
            ref_authors = reference.get("authors", "") or ""
            first_author = get_first_author_lastname(ref_authors)

            # Check Database Cache Non-Blocking
            cached_data = await self.cache.get(ref_title, first_author, ref_year)
            if cached_data:
                cached_data["cached"] = True
                return cid, cached_data

            # Build search query — use title if available, otherwise use claim for context
            if ref_title.strip():
                query = f"{ref_title} {first_author} {ref_year}".strip()
            else:
                claim_snippet = (cit.get("claim", "") or "")[:80]
                query = f"{ref_authors} {ref_year} {claim_snippet}".strip()
            if not query:
                return cid, {"status": "error", "reason": "Insufficient query parameters"}

            try:
                results = await self.semantic_scholar.search(query, limit=5)
                match, score = self._find_best_match(reference, results)

                # Fallback to CrossRef if Semantic Scholar found nothing
                if not match and ref_title.strip():
                    try:
                        cr_results = await self.crossref.search(ref_title, limit=3)
                        if cr_results:
                            match, score = self._find_best_match(reference, cr_results)
                            if match:
                                match["_source"] = "crossref"
                    except Exception:
                        pass

                if not match:
                    response_data = {
                        "status": "not_found",
                        "reason": "Not found in Semantic Scholar or CrossRef",
                        "query_used": query,
                        "search_results": len(results),
                        "cached": False
                    }
                    return cid, response_data
                    
                paper_id = match.get("paperId", "")
                
                paper_obj = {
                    "paper_id": paper_id,
                    "title": match.get("title"),
                    "authors": [a.get("name") if isinstance(a, dict) else a for a in match.get("authors", [])],
                    "year": match.get("year"),
                    "abstract": match.get("abstract", "")
                }
                
                meta_verify = self._verify_metadata(reference, paper_obj, score)

                response_data = {
                    "status": "found",
                    "paper": paper_obj,
                    "match_score": score,
                    "metadata_status": meta_verify["metadata_status"],
                    "metadata_errors": meta_verify["metadata_errors"],
                    "cached": False
                }
                
                # Write to DB asynchronously
                await self.cache.set(ref_title, first_author, ref_year, paper_id, response_data)
                return cid, response_data
                
            except APIError as e:
                return cid, {
                    "status": "not_found", 
                    "reason": f"API Error: {str(e)}"
                }
            except Exception as e:
                logging.error(f"Existence checker error (Cid {cid}): {e}")
                return cid, {"status": "error", "reason": f"Unhandled exception: {str(e)}"}

    async def process(self, ctx: PipelineContext) -> AgentResult:
        await self.cache.init_db()
        semaphore = asyncio.Semaphore(self.concurrency_limit)
        
        # Launch concurrent bounded tasks instead of a strict sequential loop
        tasks = [
            self._process_single_citation(cit, semaphore) 
            for cit in ctx.citations
        ]
        
        results_tuples = await asyncio.gather(*tasks)
        
        # Merge results into final dict exactly mimicking old output schema
        all_results = {str(cid): res for cid, res in results_tuples}
                
        return AgentResult(
            agent_name=self.name,
            status="success",
            data={"results": all_results},
            tokens_used=0,
        )

registry.register(ExistenceAgent())
