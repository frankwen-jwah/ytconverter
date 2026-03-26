"""URL classification — distinguish YouTube URLs from web articles."""

from typing import List, Tuple

ContentType = str  # "youtube" or "article"

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


def classify_url(url: str) -> ContentType:
    """Return ``"youtube"`` for YouTube URLs, ``"article"`` for everything else."""
    url_lower = url.lower().strip()
    for pattern in _YOUTUBE_PATTERNS:
        if pattern in url_lower:
            return "youtube"
    return "article"


def classify_urls(urls: List[str]) -> List[Tuple[str, ContentType]]:
    """Tag each URL with its content type."""
    return [(url, classify_url(url)) for url in urls]
