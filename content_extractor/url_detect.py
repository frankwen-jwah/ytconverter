"""URL classification — distinguish YouTube, PDF, podcast, tweet, local file, and web article URLs."""

import pathlib
from typing import List, Optional, Tuple

ContentType = str  # "youtube", "pdf", "podcast", "twitter", "local_file", or "article"

_LOCAL_FILE_EXTENSIONS = {".md", ".txt", ".docx", ".doc", ".html", ".htm", ".pptx", ".ppt"}

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

_TWITTER_PATTERNS = (
    "twitter.com/",
    "x.com/",
    "nitter.",
)

_PODCAST_PATTERNS = (
    "podcasts.apple.com/",
    "open.spotify.com/episode/",
    "open.spotify.com/show/",
    "feeds.megaphone.fm/",
    "anchor.fm/",
    "podcasters.spotify.com/",
)

# High-confidence RSS signals — match unconditionally
_RSS_STRONG_HINTS = ("/rss", ".rss", "?format=rss", "&format=rss")
# Ambiguous signals — only match when a podcast keyword is also present
_RSS_WEAK_HINTS = (".xml", "/feed")
_PODCAST_KEYWORDS = ("podcast", "episode", "feeds.", "anchor.fm", "megaphone",
                     "show", "series", "itunes")


def _is_tweet_url(url_lower: str) -> bool:
    """Return True if *url* is a tweet/post (contains /status/)."""
    for pattern in _TWITTER_PATTERNS:
        if pattern in url_lower and "/status/" in url_lower:
            return True
    return False


def is_rss_feed_url(url: str) -> bool:
    """Heuristic: URL looks like a podcast RSS feed."""
    url_lower = url.lower().strip()
    if any(hint in url_lower for hint in _RSS_STRONG_HINTS):
        return True
    if any(hint in url_lower for hint in _RSS_WEAK_HINTS):
        return any(kw in url_lower for kw in _PODCAST_KEYWORDS)
    return False


def classify_url(url: str) -> ContentType:
    """Return ``"youtube"``, ``"pdf"``, ``"twitter"``, ``"podcast"``, or ``"article"``."""
    url_lower = url.lower().strip()
    for pattern in _YOUTUBE_PATTERNS:
        if pattern in url_lower:
            return "youtube"
    for pattern in _ARXIV_PATTERNS:
        if pattern in url_lower:
            return "pdf"
    if url_lower.endswith(".pdf"):
        return "pdf"
    # Twitter/X — only actual post URLs (must contain /status/)
    if _is_tweet_url(url_lower):
        return "twitter"
    # Podcast platform URLs
    for pattern in _PODCAST_PATTERNS:
        if pattern in url_lower:
            return "podcast"
    # RSS feed heuristic
    if is_rss_feed_url(url_lower):
        return "podcast"
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
