import asyncio
import csv
import datetime
import io
import os
import sys
import time
import unicodedata
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Iterator
import httpx
import pymupdf

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "mcp-brl-congress/0.1.0",
}

CACHE_DIR = Path(
    os.environ.get("TRIBUNUS_CACHE_DIR")
    or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tribunus-brasil-mcp"
)

# Bulk files for the current year are republished daily; past years change rarely
# (late reimbursements), so they are refreshed monthly.
CURRENT_YEAR_MAX_AGE = 24 * 3600
PAST_YEAR_MAX_AGE = 30 * 24 * 3600


# Government APIs throttle bursts (HTTP 429); keep a few requests per host in flight.
HOST_CONCURRENCY = 4
RETRY_STATUS = {429, 500, 502, 503, 504}
_host_semaphores: dict[tuple[int, str], asyncio.Semaphore] = {}


def _host_slot(url: str) -> asyncio.Semaphore:
    # asyncio primitives belong to one event loop, so key them by loop as well.
    key = (id(asyncio.get_running_loop()), urllib.parse.urlsplit(url).netloc)
    return _host_semaphores.setdefault(key, asyncio.Semaphore(HOST_CONCURRENCY))


async def make_request(url: str, params: dict[str, Any] | None = None, attempts: int = 3) -> Any | None:
    """Makes a request to an API for JSON data, retrying throttled or failed responses."""
    for attempt in range(1, attempts + 1):
        async with _host_slot(url):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url, headers=DEFAULT_HEADERS, params=params, timeout=30.0, follow_redirects=True
                    )
                if response.status_code in RETRY_STATUS and attempt < attempts:
                    raise httpx.HTTPStatusError("retryable", request=response.request, response=response)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                if attempt == attempts:
                    print(f"Error fetching JSON from {url}: {e}", file=sys.stderr)
                    return None
        await asyncio.sleep(2 * attempt)
    return None


async def fetch_all_pages(
    url: str,
    params: dict[str, Any] | None = None,
    max_pages: int = 50,
) -> tuple[list[Any], bool]:
    """
    Follow the Câmara API 'next' links until exhausted.

    Returns (records, complete). `complete` is False when a page failed or `max_pages`
    was reached, so callers can report partial coverage instead of silently undercounting.
    """
    page_params = dict(params or {})
    page_params.setdefault("itens", 100)
    records: list[Any] = []

    for page in range(1, max_pages + 1):
        page_params["pagina"] = page
        data = await make_request(url, params=page_params)
        if data is None:
            return records, False
        records.extend(data.get("dados", []))
        links = data.get("links", [])
        if not any(link.get("rel") == "next" for link in links):
            return records, True

    return records, False


def cache_max_age(year: int) -> int:
    return CURRENT_YEAR_MAX_AGE if year >= datetime.date.today().year else PAST_YEAR_MAX_AGE


_download_locks: dict[tuple[int, Path], asyncio.Lock] = {}


async def fetch_cached_file(
    url: str,
    filename: str,
    max_age: int,
    attempts: int = 3,
    headers: dict[str, str] | None = None,
) -> Path | None:
    """
    Download a bulk open-data file into the local cache, reusing it while fresh.

    Concurrent callers for the same file share one download. A stale copy is still returned
    if the refresh fails, since slightly outdated data is preferable to none; callers learn
    the file age through `cached_file_age_hours`.
    """
    path = CACHE_DIR / filename
    lock = _download_locks.setdefault((id(asyncio.get_running_loop()), path), asyncio.Lock())
    async with lock:
        if path.exists() and time.time() - path.stat().st_mtime < max_age:
            return path

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".part")
        for attempt in range(1, attempts + 1):
            try:
                async with _host_slot(url), httpx.AsyncClient() as client:
                    async with client.stream(
                        "GET", url, headers={"User-Agent": DEFAULT_HEADERS["User-Agent"], **(headers or {})},
                        timeout=300.0, follow_redirects=True,
                    ) as response:
                        response.raise_for_status()
                        with open(tmp_path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                f.write(chunk)
                tmp_path.replace(path)
                return path
            except Exception as e:
                # Large government files often drop mid-transfer; retry before giving up.
                print(f"Error downloading bulk file from {url} (attempt {attempt}/{attempts}): {e}", file=sys.stderr)
                tmp_path.unlink(missing_ok=True)
                if attempt < attempts:
                    await asyncio.sleep(2 * attempt)
        return path if path.exists() else None


def cached_file_age_hours(path: Path) -> float:
    return round((time.time() - path.stat().st_mtime) / 3600, 1)


def iter_csv_rows(path: Path, member: str | None = None, encoding: str = "utf-8-sig") -> Iterator[dict[str, str]]:
    """Stream rows from a ';'-separated CSV, optionally inside a zip (first member by default)."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            with zf.open(member or zf.namelist()[0]) as raw:
                text = io.TextIOWrapper(raw, encoding=encoding, newline="")
                yield from csv.DictReader(text, delimiter=";")
    else:
        with open(path, encoding=encoding, newline="") as f:
            yield from csv.DictReader(f, delimiter=";")


def parse_brl_number(val: Any) -> float:
    """Parse numbers in either '1234.5' or Brazilian '1.234,50' notation."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        clean = str(val).replace(".", "").replace(",", ".") if "," in str(val) else str(val)
        return float(clean)
    except (ValueError, TypeError):
        return 0.0


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def ratio(part: float, whole: float) -> float | None:
    """Share rounded to 4 places, or None when the base is empty (never a fake 0%)."""
    return round(part / whole, 4) if whole else None


def normalize_name(name: str) -> str:
    """Lowercase and strip accents so 'Átila' and 'atila' compare equal."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).lower().split())


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
