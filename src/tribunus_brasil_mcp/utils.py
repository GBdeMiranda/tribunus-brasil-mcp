import io
import sys
from typing import Any
import httpx
import pymupdf

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "mcp-brl-congress/0.1.0",
}


async def make_request(url: str, params: dict[str, Any] | None = None) -> Any | None:
    """Makes a request to an API for JSON data."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                url, headers=DEFAULT_HEADERS, params=params, timeout=30.0, follow_redirects=True
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching JSON from {url}: {e}", file=sys.stderr)
            return None


async def fetch_document_content(url: str) -> bytes | None:
    """Makes a request to fetch raw document bytes."""
    headers = {"User-Agent": "mcp-brl-congress/0.1.0"}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except Exception as e:
            print(f"Error downloading document from {url}: {e}", file=sys.stderr)
            return None


def extract_text_from_pdf(pdf_bytes: bytes, max_chars: int = 40000) -> str:
    """Extracts text from a PDF with an optional character limit."""
    try:
        with pymupdf.open(stream=io.BytesIO(pdf_bytes), filetype="pdf") as doc:
            full_text = "".join(page.get_text() for page in doc).strip()
        return full_text[:max_chars]
    except Exception as e:
        print(f"Error extracting text from PDF: {e}", file=sys.stderr)
        return ""