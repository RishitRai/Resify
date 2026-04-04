import re
import time
from server.agents.base import BaseAgent, AgentResult, PipelineContext, PipelineStage, registry
from server.utils.apis import SemanticScholarAPI, ArXivAPI, CrossRefAPI, APIError
from server.utils.pdf import PDFScraper

class FetcherAgent(BaseAgent):
    name = "fetcher"
    stage = PipelineStage.FETCHING
    description = "Fetch and extract paper text"
    
    def __init__(self):
        super().__init__()
        from server.config import settings
        self.arxiv_api = ArXivAPI()
        self.crossref_api = CrossRefAPI()
        self.semantic_scholar = SemanticScholarAPI(api_key=settings.S2_API_KEY or None)

    def _detect_input_type(self, input_str: str) -> str:
        if "arxiv.org" in input_str or re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", input_str):
            return "arxiv"
        elif input_str.startswith("10.") or "doi.org" in input_str:
            return "doi"
        elif input_str.endswith(".pdf"):
            return "pdf"
        elif input_str.startswith("http://") or input_str.startswith("https://"):
            return "url" # fallback generic URL, maybe text
        else:
            return "direct"

    def _extract_arxiv_id(self, input_str: str) -> str:
        match = re.search(r"(\d{4}\.\d{4,5}(v\d+)?)", input_str)
        return match.group(1) if match else input_str

    def _extract_doi(self, input_str: str) -> str:
        match = re.search(r"(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)", input_str)
        return match.group(1) if match else input_str

    async def process(self, ctx: PipelineContext) -> AgentResult:
        # We can try paper_url, paper_doi, or paper_text
        input_str = ctx.paper_url or ctx.paper_doi or ctx.paper_text
        if not input_str:
            return AgentResult(agent_name=self.name, status="error", error="No input provided")
            
        input_type = self._detect_input_type(input_str)
        
        data = {
            "text": None,
            "title": "Document Title", # Fallback title
            "authors": [],
            "year": None,
            "abstract": None,
            "source": input_type,
            "url": ctx.paper_url,
            "arxiv_id": None,
            "doi": ctx.paper_doi,
            "pdf_url": None
        }

        if input_type == "arxiv":
            arxiv_id = self._extract_arxiv_id(input_str)
            data["arxiv_id"] = arxiv_id
            try:
                paper_metadata = await self.arxiv_api.get_paper(arxiv_id)
                if paper_metadata:
                    data.update(paper_metadata)
                    data["doi"] = f"10.48550/arXiv.{arxiv_id}"
                    if data.get("pdf_url"):
                        data["text"] = await PDFScraper.extract_text_from_url(data["pdf_url"])
                else:
                    raise APIError("Paper not found on ArXiv", 404)
            except Exception as primary_e:
                try:
                    # Semantic Scholar Fallback
                    ss_data = await self.semantic_scholar.get_paper(f"ARXIV:{arxiv_id}")
                    if ss_data:
                        data.update({
                            "title": ss_data.get("title", data["title"]),
                            "year": ss_data.get("year"),
                            "abstract": ss_data.get("abstract"),
                            "authors": [a.get("name") for a in ss_data.get("authors", [])] if ss_data.get("authors") else [],
                            "doi": ss_data.get("externalIds", {}).get("DOI", f"10.48550/arXiv.{arxiv_id}")
                        })
                    else:
                        raise Exception(f"FETCH_FAILED (and fallback failed): {str(primary_e)}")
                except Exception as fallback_e:
                    raise Exception(f"FETCH_FAILED: {str(primary_e)}. Fallback also failed: {str(fallback_e)}")
                
        elif input_type == "doi":
            doi = self._extract_doi(input_str)
            data["doi"] = doi
            try:
                paper_metadata = await self.crossref_api.get_paper_by_doi(doi)
                if paper_metadata:
                    data.update(paper_metadata)
                
                # If crossref was missing abstract, try gracefully plugging it in
                if paper_metadata and not data.get("abstract"):
                    ss_data = await self.semantic_scholar.get_paper_by_doi(doi)
                    if ss_data and ss_data.get("abstract"):
                        data["abstract"] = ss_data["abstract"]
                        
                if not paper_metadata:
                    raise APIError("Paper not found on CrossRef", 404)
                    
            except Exception as primary_e:
                try:
                    # Complete Semantic Scholar Fallback
                    ss_data = await self.semantic_scholar.get_paper_by_doi(doi)
                    if ss_data:
                        data.update({
                            "title": ss_data.get("title", data["title"]),
                            "year": ss_data.get("year"),
                            "abstract": ss_data.get("abstract"),
                            "authors": [a.get("name") for a in ss_data.get("authors", [])] if ss_data.get("authors") else []
                        })
                    else:
                        raise Exception(f"FETCH_FAILED (and fallback failed): {str(primary_e)}")
                except Exception as fallback_e:
                    raise Exception(f"FETCH_FAILED: {str(primary_e)}. Fallback also failed: {str(fallback_e)}")

        elif input_type == "pdf":
            try:
                data["text"] = await PDFScraper.extract_text_from_url(input_str)
            except Exception as e:
                raise Exception("PDF_PARSE_FAILED: " + str(e))

        elif input_type == "direct" or input_type == "url":
            # If it's pure text, just pass it through
            data["text"] = input_str
            data["source"] = "direct"
        
        return AgentResult(
            agent_name=self.name,
            status="success",
            data=data,
            tokens_used=0,
        )

registry.register(FetcherAgent())
