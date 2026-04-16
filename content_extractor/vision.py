"""Image description via Azure OpenAI vision — extract info from images."""

import logging
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from uuid import uuid4

from .config import OUTPUT_DIR
from .exceptions import LLMError
from .models import ExtractedImage

if TYPE_CHECKING:
    from .config import Config

# ---------------------------------------------------------------------------
# Logger — writes to content/llm.log (shared with llm.py)
# ---------------------------------------------------------------------------

_log = logging.getLogger("content_extractor.vision")
_log.setLevel(logging.DEBUG)
_log.propagate = False

_log_file = OUTPUT_DIR / "llm.log"
_log_file.parent.mkdir(parents=True, exist_ok=True)
_fh = logging.FileHandler(_log_file, encoding="utf-8")
_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_log.addHandler(_fh)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_VISION_SYSTEM = """\
You are an image description assistant. Describe the image provided.

Rules:
- For charts/graphs: describe the axes, data trends, and key findings
- For diagrams/flowcharts: describe the components and their relationships
- For tables rendered as images: transcribe the table data as text
- For photos: describe the subject, setting, and notable details
- For screenshots: read and transcribe visible text, describe UI elements
- Transcribe any significant visible text
- Be concise: 1-4 sentences for simple images, more for data-rich content
- Output ONLY the description text, no commentary or formatting"""


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------

def make_image_marker() -> str:
    """Generate a unique image placeholder marker."""
    return f"<!--IMG:{uuid4()}-->"


_MARKER_PATTERN = re.compile(r"<!--IMG:[0-9a-f\-]+-->")


def replace_image_markers(text: str, descriptions: Dict[str, str]) -> str:
    """Replace image position markers with formatted descriptions.

    Markers with no corresponding description get a fallback notice.
    """
    def _replace(match):
        marker = match.group(0)
        desc = descriptions.get(marker)
        if desc:
            return f"\n\n> [Image: {desc.strip()}]\n"
        return "\n\n> [Image: description unavailable]\n"

    return _MARKER_PATTERN.sub(_replace, text)


# ---------------------------------------------------------------------------
# MIME type helper
# ---------------------------------------------------------------------------

_FORMAT_TO_MIME = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def _call_vision(
    image_bytes: bytes,
    image_format: str,
    source_label: str,
    alt_text: str,
    config: "Config",
) -> str:
    """Describe a single image via Azure OpenAI vision. Returns description text."""
    from . import llm_backend

    # Build user message
    context_parts = [f"Source: {source_label}"]
    if alt_text:
        context_parts.append(f"Alt text: {alt_text}")
    context = ". ".join(context_parts)

    user_msg = (
        f"Describe what you see in this image.\n"
        f"Context: {context}\n"
        f"Provide a concise description suitable for a reader who cannot see the image."
    )

    mime_type = _FORMAT_TO_MIME.get(image_format, "image/png")

    msg = f"vision: {source_label} ({len(image_bytes)} bytes, {mime_type})"
    print(f"    [vision] {msg}", flush=True)
    _log.info(msg)
    t0 = time.time()

    try:
        model = config.vision.model or None  # None = use default deployment
        output = llm_backend.vision_completion(
            _VISION_SYSTEM, user_msg, image_bytes,
            mime_type=mime_type, model=model,
        )
    except LLMError:
        raise
    except Exception as e:
        elapsed = time.time() - t0
        msg = f"Vision API failed after {elapsed:.1f}s: {e}"
        print(f"    [vision] ERROR: {msg}", flush=True)
        _log.error(msg)
        raise LLMError(msg) from e

    elapsed = time.time() - t0
    msg = f"returned in {elapsed:.1f}s | {len(output)} chars"
    print(f"    [vision] {msg}", flush=True)
    _log.info(f"description: {len(output)} chars in {elapsed:.1f}s")
    _log.debug(f"description preview: {output[:200]}")
    return output


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def describe_images(
    images: List[ExtractedImage],
    config: "Config",
) -> Dict[str, str]:
    """Describe images using Azure OpenAI vision. Returns {position_marker: description}.

    Filters by size thresholds, caps at max_images, runs in parallel.
    Per-image errors are logged and skipped (marker maps to empty string).
    """
    if not images:
        return {}

    vc = config.vision

    # Filter by size thresholds
    filtered = []
    for img in images:
        if img.width > 0 and img.width < vc.min_width:
            _log.debug(f"skip {img.source_label}: width {img.width} < {vc.min_width}")
            continue
        if img.height > 0 and img.height < vc.min_height:
            _log.debug(f"skip {img.source_label}: height {img.height} < {vc.min_height}")
            continue
        if len(img.image_bytes) < vc.min_bytes:
            _log.debug(f"skip {img.source_label}: {len(img.image_bytes)} bytes < {vc.min_bytes}")
            continue
        filtered.append(img)

    # Cap at max_images
    if vc.max_images > 0 and len(filtered) > vc.max_images:
        _log.info(f"capping images from {len(filtered)} to {vc.max_images}")
        filtered = filtered[:vc.max_images]

    if not filtered:
        return {}

    print(f"  [vision] Describing {len(filtered)} image(s)...", flush=True)

    # Build work items — pass image bytes directly (no temp files needed)
    work_items: List[Tuple[str, ExtractedImage]] = [
        (img.position_marker, img) for img in filtered
    ]

    descriptions: Dict[str, str] = {}
    max_workers = min(vc.max_workers, len(work_items))

    if len(work_items) == 1:
        marker, img = work_items[0]
        fmt = img.format if img.format in _FORMAT_TO_MIME else "png"
        try:
            descriptions[marker] = _call_vision(
                img.image_bytes, fmt, img.source_label, img.alt_text, config)
        except LLMError as e:
            _log.warning(f"Vision failed for {img.source_label}: {e}")
            print(f"    [vision] WARNING: {img.source_label}: {e}", flush=True)
    else:
        print(f"  [vision] Processing {len(work_items)} images "
              f"({max_workers} workers)...", flush=True)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_marker = {}
            for marker, img in work_items:
                fmt = img.format if img.format in _FORMAT_TO_MIME else "png"
                future = executor.submit(
                    _call_vision,
                    img.image_bytes, fmt, img.source_label, img.alt_text, config,
                )
                future_to_marker[future] = marker

            done = 0
            for future in as_completed(future_to_marker):
                marker = future_to_marker[future]
                done += 1
                try:
                    descriptions[marker] = future.result()
                    print(f"  [vision] Completed {done}/{len(work_items)}",
                          flush=True)
                except LLMError as e:
                    _log.warning(f"Vision failed for marker {marker}: {e}")
                    print(f"    [vision] WARNING: image {done}: {e}",
                          flush=True)

    return descriptions
