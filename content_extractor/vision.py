"""Image description via Claude vision — extract info from images using Claude CLI."""

import logging
import pathlib
import re
import shutil
import subprocess
import tempfile
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
You are an image description assistant. You will be given a file path to an image.
Use the Read tool to view the image, then provide a clear, concise description.

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
# Claude CLI call for vision
# ---------------------------------------------------------------------------

def _run_claude_vision_cli(
    model: str,
    system: str,
    user_msg: str,
    image_dir: str,
    fallback: Optional[str] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run Claude CLI with Read tool access to an image directory."""
    from . import llm as _llm

    claude_path = _llm._claude_path
    if not claude_path:
        raise LLMError("LLM not initialized. Call init_llm() first.")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(system)
        system_file = f.name

    try:
        cmd = [
            claude_path, "-p",
            "--output-format", "json",
            "--model", model,
            "--max-turns", "2",
            "--system-prompt-file", system_file,
            "--tools", "Read",
            "--add-dir", image_dir,
        ]
        if fallback:
            cmd.extend(["--fallback-model", fallback])

        return subprocess.run(
            cmd,
            input=user_msg,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    finally:
        pathlib.Path(system_file).unlink(missing_ok=True)


def _parse_json_output(stdout: str) -> str:
    """Extract text result from JSON output."""
    import json
    try:
        data = json.loads(stdout)
        return data.get("result", "")
    except (json.JSONDecodeError, TypeError):
        return stdout.strip()


def _call_vision(
    image_path: str,
    source_label: str,
    alt_text: str,
    config: "Config",
) -> str:
    """Describe a single image via Claude CLI. Returns description text."""
    from . import llm as _llm

    with _llm._model_lock:
        model = config.vision.model or _llm._model_alias
        fallback = _llm._fallback_alias

    # Build user message
    context_parts = [f"Source: {source_label}"]
    if alt_text:
        context_parts.append(f"Alt text: {alt_text}")
    context = ". ".join(context_parts)

    # Use forward slashes for the path (Claude CLI compatibility)
    norm_path = image_path.replace("\\", "/")
    user_msg = (
        f"Read the image file at {norm_path} and describe what you see.\n"
        f"Context: {context}\n"
        f"Provide a concise description suitable for a reader who cannot see the image."
    )

    image_dir = str(pathlib.Path(image_path).parent)
    timeout = config.vision.timeout

    msg = f"vision: {model} | {source_label} | {pathlib.Path(image_path).name}"
    print(f"    [vision] {msg}", flush=True)
    _log.info(msg)
    t0 = time.time()

    try:
        result = _run_claude_vision_cli(
            model, _VISION_SYSTEM, user_msg, image_dir,
            fallback=fallback, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        msg = f"TIMEOUT after {elapsed:.0f}s"
        print(f"    [vision] {msg}", flush=True)
        _log.error(msg)
        raise LLMError(f"Vision CLI timed out after {timeout}s")
    except FileNotFoundError:
        _log.error("Claude CLI binary not found")
        raise LLMError("Claude CLI not found")

    elapsed = time.time() - t0
    msg = f"returned in {elapsed:.1f}s | exit={result.returncode}"
    print(f"    [vision] {msg}", flush=True)
    _log.info(msg)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        msg = f"Vision CLI failed (exit {result.returncode}): {stderr[:200]}"
        _log.error(msg)
        raise LLMError(msg)

    raw = result.stdout.strip()
    if not raw:
        raise LLMError("Vision CLI returned empty output")

    output = _parse_json_output(raw)
    if not output:
        raise LLMError("Vision CLI returned empty result")

    _log.info(f"description: {len(output)} chars")
    _log.debug(f"description preview: {output[:200]}")
    return output


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def describe_images(
    images: List[ExtractedImage],
    config: "Config",
) -> Dict[str, str]:
    """Describe images using Claude vision. Returns {position_marker: description}.

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

    # Write all images to a single temp directory
    temp_dir = tempfile.mkdtemp(prefix="vision_")
    image_paths: Dict[str, str] = {}  # marker → file path

    try:
        for i, img in enumerate(filtered):
            ext = img.format if img.format in ("png", "jpeg", "jpg", "gif", "webp") else "png"
            fname = f"img_{i:03d}.{ext}"
            fpath = pathlib.Path(temp_dir) / fname
            fpath.write_bytes(img.image_bytes)
            image_paths[img.position_marker] = str(fpath)

        # Build work items
        work_items: List[Tuple[str, ExtractedImage]] = [
            (img.position_marker, img) for img in filtered
        ]

        # Execute (parallel or single)
        descriptions: Dict[str, str] = {}
        max_workers = min(vc.max_workers, len(work_items))

        if len(work_items) == 1:
            marker, img = work_items[0]
            try:
                descriptions[marker] = _call_vision(
                    image_paths[marker], img.source_label, img.alt_text, config)
            except LLMError as e:
                _log.warning(f"Vision failed for {img.source_label}: {e}")
                print(f"    [vision] WARNING: {img.source_label}: {e}", flush=True)
        else:
            print(f"  [vision] Processing {len(work_items)} images "
                  f"({max_workers} workers)...", flush=True)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_marker = {
                    executor.submit(
                        _call_vision,
                        image_paths[marker],
                        img.source_label,
                        img.alt_text,
                        config,
                    ): marker
                    for marker, img in work_items
                }
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

    finally:
        # Clean up temp directory (tolerate Windows permission errors)
        try:
            shutil.rmtree(temp_dir)
        except (PermissionError, OSError):
            _log.debug(f"Could not remove temp dir: {temp_dir}")
