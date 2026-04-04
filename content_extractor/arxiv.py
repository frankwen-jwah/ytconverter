"""ArXiv URL resolution and metadata fetching via the Atom API."""

import re
from typing import List, Optional, Tuple, TYPE_CHECKING
from xml.etree import ElementTree

from .exceptions import ArxivAPIError
from .models import ArticleSection, PDFInfo

if TYPE_CHECKING:
    from .config import NetworkConfig

_ARXIV_ID_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)"
)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

_API_BASE = "http://export.arxiv.org/api/query"


def extract_arxiv_id(url: str) -> Optional[str]:
    """Extract arXiv paper ID from a URL.  Returns ``None`` if not an arxiv URL."""
    m = _ARXIV_ID_RE.search(url)
    return m.group(1) if m else None


def normalize_arxiv_url(url: str) -> Tuple[str, str]:
    """Return ``(abs_url, pdf_url)`` from any arXiv URL variant.

    Raises ``ArxivAPIError`` if no arXiv ID can be extracted.
    """
    arxiv_id = extract_arxiv_id(url)
    if not arxiv_id:
        raise ArxivAPIError(f"Could not extract arXiv ID from: {url}")
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    return abs_url, pdf_url


def fetch_arxiv_metadata(arxiv_id: str,
                         network_config: "NetworkConfig") -> dict:
    """Fetch metadata from the arXiv Atom API.

    Returns a dict with keys: ``title``, ``authors``, ``abstract``,
    ``categories``, ``publish_date``, ``doi``, ``arxiv_id``.
    """
    from .deps import ensure_requests
    ensure_requests()
    import requests

    from .retry import retry_with_backoff

    api_url = f"{_API_BASE}?id_list={arxiv_id}"

    def _attempt():
        resp = requests.get(api_url, timeout=30)
        resp.raise_for_status()
        return resp.text

    try:
        xml_text = retry_with_backoff(
            _attempt,
            retries=network_config.retries,
            backoff_base=network_config.backoff_base,
        )
    except Exception as exc:
        raise ArxivAPIError(
            f"Failed to fetch arXiv metadata for {arxiv_id}: {exc}"
        ) from exc

    return _parse_atom_response(xml_text, arxiv_id)


def _parse_atom_response(xml_text: str, arxiv_id: str) -> dict:
    """Parse an arXiv Atom API XML response into a metadata dict."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ArxivAPIError(f"Failed to parse arXiv API response: {exc}") from exc

    entry = root.find(f"{_ATOM_NS}entry")
    if entry is None:
        raise ArxivAPIError(f"No entry found in arXiv API response for {arxiv_id}")

    # Check for API error (entry with no actual paper data)
    id_elem = entry.find(f"{_ATOM_NS}id")
    if id_elem is not None and "api/errors" in (id_elem.text or ""):
        summary = entry.findtext(f"{_ATOM_NS}summary", "").strip()
        raise ArxivAPIError(f"arXiv API error for {arxiv_id}: {summary}")

    title = entry.findtext(f"{_ATOM_NS}title", "").strip()
    # Normalise whitespace (arXiv titles often span multiple lines)
    title = re.sub(r"\s+", " ", title)

    authors = []
    for author_elem in entry.findall(f"{_ATOM_NS}author"):
        name = author_elem.findtext(f"{_ATOM_NS}name", "").strip()
        if name:
            authors.append(name)

    abstract = entry.findtext(f"{_ATOM_NS}summary", "").strip()
    abstract = re.sub(r"\s+", " ", abstract)

    # Published date → YYYY-MM-DD
    published = entry.findtext(f"{_ATOM_NS}published", "")
    publish_date = published[:10] if len(published) >= 10 else "unknown"

    # Categories
    categories: List[str] = []
    for cat_elem in entry.findall(f"{_ATOM_NS}category"):
        term = cat_elem.get("term", "")
        if term:
            categories.append(term)

    # DOI (arXiv-specific namespace)
    doi = entry.findtext(f"{_ARXIV_NS}doi", None)
    if doi:
        doi = doi.strip()

    return {
        "title": title or "Untitled",
        "authors": authors,
        "abstract": abstract,
        "categories": categories,
        "publish_date": publish_date,
        "doi": doi,
        "arxiv_id": arxiv_id,
    }


def build_pdf_info_from_arxiv(
    metadata: dict,
    sections: List[ArticleSection],
    page_count: int,
    word_count: int,
) -> PDFInfo:
    """Construct a ``PDFInfo`` from arXiv API metadata + extracted content."""
    arxiv_id = metadata["arxiv_id"]
    return PDFInfo(
        title=metadata["title"],
        url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        authors=metadata["authors"],
        publish_date=metadata["publish_date"],
        language="en",  # arXiv papers are overwhelmingly English
        abstract=metadata["abstract"],
        categories=metadata["categories"],
        arxiv_id=arxiv_id,
        doi=metadata["doi"],
        page_count=page_count,
        word_count=word_count,
        sections=sections,
    )
