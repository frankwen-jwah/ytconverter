"""Metadata parsing — convert yt-dlp JSON to typed data classes."""

from typing import List

from .models import Chapter, VideoInfo


def format_duration(seconds: int) -> str:
    """Convert seconds to H:MM:SS or M:SS."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_upload_date(raw: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD."""
    if raw and len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw or "unknown"


def extract_video_info(meta: dict) -> VideoInfo:
    """Parse yt-dlp JSON metadata into VideoInfo."""
    chapters: List[Chapter] = []
    for ch in (meta.get("chapters") or []):
        chapters.append(Chapter(
            title=ch.get("title", "Untitled"),
            start_seconds=ch.get("start_time", 0),
            end_seconds=ch.get("end_time", 0),
        ))
    dur = int(meta.get("duration") or 0)
    return VideoInfo(
        video_id=meta.get("id", ""),
        title=meta.get("title", "Untitled"),
        url=meta.get("webpage_url", meta.get("original_url", "")),
        channel=meta.get("channel", meta.get("uploader", "Unknown")),
        upload_date=parse_upload_date(meta.get("upload_date", "")),
        duration_seconds=dur,
        duration_string=format_duration(dur),
        language=meta.get("language"),
        chapters=chapters,
        description=meta.get("description", ""),
    )
