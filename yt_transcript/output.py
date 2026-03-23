"""File output — slugify, path generation, and file writing."""

import pathlib
import re
import unicodedata

from .models import VideoInfo


def slugify(text: str, max_length: int = 80) -> str:
    """Convert text to filesystem-safe slug. Keeps CJK characters."""
    # Normalize unicode
    text = unicodedata.normalize("NFKC", text)
    # Lowercase (only affects Latin chars, CJK unaffected)
    text = text.lower()
    # Replace spaces and common separators with hyphens
    text = re.sub(r"[\s_]+", "-", text)
    # Keep alphanumeric, hyphens, and CJK characters
    text = re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)
    # Collapse multiple hyphens
    text = re.sub(r"-{2,}", "-", text)
    # Strip leading/trailing hyphens
    text = text.strip("-")
    # Truncate
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text or "untitled"


def make_output_path(info: VideoInfo, output_dir: pathlib.Path,
                     suffix: str = ".md") -> pathlib.Path:
    """Generate output path: output_dir/YYYY-MM-DD_slug.md with collision handling."""
    slug = slugify(info.title)
    base_name = f"{info.upload_date}_{slug}"
    path = output_dir / f"{base_name}{suffix}"

    counter = 2
    while path.exists():
        path = output_dir / f"{base_name}-{counter}{suffix}"
        counter += 1

    return path


def save_transcript(markdown: str, path: pathlib.Path, overwrite: bool) -> pathlib.Path:
    """Write markdown to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        print(f"  File exists, skipping: {path.name}")
        return path
    path.write_text(markdown, encoding="utf-8")
    return path
