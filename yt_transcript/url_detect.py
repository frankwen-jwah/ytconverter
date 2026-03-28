"""URL classification — distinguish YouTube, PDF, local file, and web article URLs."""

import pathlib
from typing import List, Optional, Tuple

ContentType = str  # "youtube", "pdf", "local_file", or "article"

_LOCAL_FILE_EXTENSIONS = {".md", ".txt", ".docx", ".doc", ".html", ".htm"}

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


def strip_path_quotes(s: str) -> str:
    """Strip accidental quote wrappers from a path string.

    Handles Python raw-string syntax (``r"..."``), bare quotes, and
    trailing/leading whitespace that shells sometimes leave behind.
    """
    s = s.strip()
    # r"path" or r'path' — user mistakenly used Python raw-string syntax
    if (s.startswith('r"') and s.endswith('"')) or (s.startswith("r'") and s.endswith("'")):
        s = s[2:-1]
    # Bare quotes around the whole path
    elif (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    return s


def classify_local_path(path_str: str) -> Optional[ContentType]:
    """If *path_str* refers to an existing local file, return its content type.

    Returns ``"pdf"`` for .pdf, ``"local_file"`` for supported text formats,
    or ``None`` if it's not a recognized local file.
    """
    p = pathlib.Path(path_str)
    if not p.exists() or not p.is_file():
        return None
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in _LOCAL_FILE_EXTENSIONS:
        return "local_file"
    return None


def classify_urls(urls: List[str]) -> List[Tuple[str, ContentType]]:
    """Tag each URL with its content type."""
    return [(url, classify_url(url)) for url in urls]
