"""Pipeline orchestration — single local file processing and dry-run."""

import pathlib
from typing import TYPE_CHECKING

from .article import sections_to_body_text
from .exceptions import PipelineError
from .local_file import extract_local_file
from .models import ArticleResult

if TYPE_CHECKING:
    from .config import Config


def process_single_local_file(file_path: str, config: "Config") -> ArticleResult:
    """Full extraction pipeline for one local file."""
    # 1. Extract content and metadata (dispatches by extension)
    info, sections, images = extract_local_file(
        file_path, config.local_files,
        extract_images=config.vision.enabled)
    print(f"{info.title}", flush=True)

    # 1b. Describe images via Claude vision
    if config.vision.enabled and images:
        from .vision import describe_images, replace_image_markers
        print(f"  [local] Describing {len(images)} image(s)...", flush=True)
        descriptions = describe_images(images, config)
        for s in sections:
            s.body = replace_image_markers(s.body, descriptions)

    # 2. Assemble body text
    body_text = sections_to_body_text(sections)

    return ArticleResult(
        info=info,
        body_text=body_text,
        sections=sections,
    )


def dry_run_local_file(file_path: str, config: "Config") -> None:
    """Print local file info without full extraction."""
    try:
        p = pathlib.Path(file_path)
        print(f"  File:      {p.name}")
        print(f"  Path:      {p.resolve()}")
        print(f"  Size:      {p.stat().st_size:,} bytes")
        print(f"  Format:    {p.suffix.lower()}")
        print()
    except (PipelineError, OSError) as e:
        print(f"  ERROR: {e}")
        print()
