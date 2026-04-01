"""File output — slugify, path generation, and file writing."""

import pathlib
import re
import shutil
import unicodedata
from datetime import datetime


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


def make_output_path(title: str, date: str, output_dir: pathlib.Path,
                     suffix: str = ".md", slug_max_length: int = 80) -> pathlib.Path:
    """Generate output path: output_dir/YYYY-MM-DD_slug.md with collision handling."""
    slug = slugify(title, max_length=slug_max_length)
    base_name = f"{date}_{slug}"
    path = output_dir / f"{base_name}{suffix}"

    counter = 2
    while path.exists():
        path = output_dir / f"{base_name}-{counter}{suffix}"
        counter += 1

    return path


def make_output_folder(title: str, date: str, output_dir: pathlib.Path,
                       slug_max_length: int = 80) -> pathlib.Path:
    """Create timestamped output folder: output_dir/output/YYYY-MM-DD_slug_YYYYMMDD-HHMM/."""
    slug = slugify(title, max_length=slug_max_length)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    folder = output_dir / "output" / f"{date}_{slug}_{timestamp}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_transcript(markdown: str, path: pathlib.Path, overwrite: bool) -> pathlib.Path:
    """Write markdown to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        print(f"  File exists, skipping: {path.name}")
        return path
    path.write_text(markdown, encoding="utf-8")
    return path


def copy_summary_to_batch(folder: pathlib.Path) -> None:
    """Copy summary.md to batch-process/ directory, named after the folder.

    E.g. content/output/2026-03-28_slug_20260331-1409/summary.md
      -> content/output/batch-process/2026-03-28_slug_20260331-1409.md
    """
    summary = folder / "summary.md"
    if not summary.exists():
        return
    batch_dir = folder.parent / "batch-process"
    batch_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(summary, batch_dir / f"{folder.name}.md")
