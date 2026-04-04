import ssl
import certifi
import aiohttp
import asyncio
import io
import fitz  # PyMuPDF

class PDFScraper:
    @staticmethod
    async def extract_text_from_url(url: str, max_chars: int = 200000) -> str:
        """Downloads a PDF from a URL and extracts its text using PyMuPDF."""
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        raise Exception(f"Failed to download PDF, status code: {response.status}")
                    
                    pdf_bytes = await response.read()
                    return PDFScraper.extract_text_from_bytes(pdf_bytes, max_chars)
            except asyncio.TimeoutError:
                raise Exception("Timeout downloading PDF")

    @staticmethod
    def extract_text_from_bytes(pdf_bytes: bytes, max_chars: int = 200000) -> str:
        """Extracts text from PDF bytes."""
        try:
            doc = fitz.open("pdf", pdf_bytes)
            text = ""
            for page in doc:
                text += page.get_text()
                if len(text) > max_chars:
                    text = text[:max_chars]
                    break
            return text
        except Exception as e:
            raise Exception(f"Failed to parse PDF: {str(e)}")
