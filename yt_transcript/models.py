"""Data classes for the content extraction pipeline."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass
class Chapter:
    title: str
    start_seconds: float
    end_seconds: float


@dataclass
class VideoInfo:
    video_id: str
    title: str
    url: str
    channel: str
    upload_date: str          # YYYY-MM-DD
    duration_seconds: int
    duration_string: str      # H:MM:SS
    language: Optional[str]
    chapters: List[Chapter]
    description: str


@dataclass
class TranscriptResult:
    info: VideoInfo
    cues: List[SubtitleCue]
    sub_language: str
    is_auto_generated: bool
    is_whisper_transcribed: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Article data classes
# ---------------------------------------------------------------------------

@dataclass
class ArticleSection:
    """A section of an article (heading + body text)."""
    heading: str
    level: int          # 1=h1, 2=h2, etc.
    body: str


@dataclass
class ArticleInfo:
    """Metadata extracted from a web article."""
    title: str
    url: str
    author: str              # "" if unknown
    site_name: str           # "" if unknown
    publish_date: str        # YYYY-MM-DD or "unknown"
    language: Optional[str]
    description: str
    word_count: int
    sections: List[ArticleSection]


@dataclass
class ArticleResult:
    """Result of processing a single article."""
    info: ArticleInfo
    body_text: str           # Full extracted text (for LLM consumption)
    sections: List[ArticleSection]
    error: Optional[str] = None
