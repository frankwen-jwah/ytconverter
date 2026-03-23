"""Data classes for the YouTube transcript pipeline."""

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
    error: Optional[str] = None
