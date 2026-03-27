"""URL classification — distinguish YouTube, PDF, and web article URLs."""

from typing import List, Tuple

ContentType = str  # "youtube", "pdf", or "article"

_YOUTUBE_PATTERNS = (
    "youtube.com/watch",
    "youtube.com/playlist",
    "youtube.com/shorts",
    "youtu.be/",
    "youtube.com/c/",
    "youtube.com/channel/",
    "youtube.com/@",
    "youtube.com/live/",
)

_ARXIV_PATTERNS = (
    "arxiv.org/abs/",
    "arxiv.org/pdf/",
    "arxiv.org/html/",
)


def classify_url(url: str) -> ContentType:
    """Return ``"youtube"``, ``"pdf"``, or ``"article"``."""
    url_lower = url.lower().strip()
    for pattern in _YOUTUBE_PATTERNS:
        if pattern in url_lower:
            return "youtube"
    for pattern in _ARXIV_PATTERNS:
        if pattern in url_lower:
            return "pdf"
    if url_lower.endswith(".pdf"):
        return "pdf"
    return "article"


def is_arxiv_url(url: str) -> bool:
    """Return True if *url* points to an arXiv resource."""
    url_lower = url.lower().strip()
    return any(p in url_lower for p in _ARXIV_PATTERNS)


def classify_urls(urls: List[str]) -> List[Tuple[str, ContentType]]:
    """Tag each URL with its content type."""
    return [(url, classify_url(url)) for url in urls]
