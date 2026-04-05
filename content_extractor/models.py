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


# ---------------------------------------------------------------------------
# PDF data classes
# ---------------------------------------------------------------------------

@dataclass
class PDFInfo:
    """Metadata for a PDF document (arxiv paper or generic PDF)."""
    title: str
    url: str                     # Source URL (arxiv abs page, or direct PDF URL)
    pdf_url: str                 # Direct link to the PDF file
    authors: List[str]
    publish_date: str            # YYYY-MM-DD or "unknown"
    language: Optional[str]
    abstract: str
    categories: List[str]        # ArXiv categories like ["cs.AI"], empty for non-arxiv
    arxiv_id: Optional[str]
    doi: Optional[str]
    page_count: int
    word_count: int
    sections: List[ArticleSection]


@dataclass
class PDFResult:
    """Result of processing a single PDF."""
    info: PDFInfo
    body_text: str               # Full extracted text (for LLM consumption)
    sections: List[ArticleSection]
    has_math: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Podcast data classes
# ---------------------------------------------------------------------------

@dataclass
class PodcastEpisodeInfo:
    """Metadata for a single podcast episode."""
    title: str
    show_name: str
    episode_number: Optional[str]   # "42" or None
    url: str                         # Episode page URL or audio URL
    audio_url: str                   # Direct audio URL
    publish_date: str                # YYYY-MM-DD
    duration_seconds: int
    duration_string: str             # H:MM:SS
    language: Optional[str]
    description: str


@dataclass
class PodcastResult:
    """Result of processing a single podcast episode."""
    info: PodcastEpisodeInfo
    cues: List[SubtitleCue]
    sub_language: str
    is_whisper_transcribed: bool = True
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Twitter/X data classes
# ---------------------------------------------------------------------------

@dataclass
class TweetInfo:
    """Metadata for a tweet/post or thread."""
    title: str              # First ~80 chars of text, for slugification
    url: str                # Original x.com/twitter.com URL
    author: str             # @handle
    author_name: str        # Display name
    publish_date: str       # YYYY-MM-DD
    word_count: int
    is_thread: bool
    thread_length: int      # Number of posts in thread
    tweet_subtype: str = "tweet"  # "tweet", "note_tweet", or "x_article"


@dataclass
class TweetResult:
    """Result of processing a single tweet/thread."""
    info: TweetInfo
    body_text: str
    sections: List[ArticleSection]  # Reuse existing ArticleSection
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Image extraction (for Claude Vision description)
# ---------------------------------------------------------------------------

@dataclass
class ExtractedImage:
    """An image extracted from a document, pending vision description."""
    image_bytes: bytes          # Raw image data (PNG/JPEG)
    format: str                 # "png" or "jpeg"
    source_label: str           # e.g. "PDF page 3", "Slide 7"
    position_marker: str        # Unique placeholder: "<!--IMG:uuid-->"
    width: int = 0              # Image width in pixels (0 = unknown)
    height: int = 0             # Image height in pixels (0 = unknown)
    alt_text: str = ""          # Alt text from source if available
