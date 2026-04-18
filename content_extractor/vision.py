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
You are a technical image description assistant. Your output is embedded directly into
a Markdown document, so format for Markdown renderers (GitHub, Obsidian, MkDocs — all
of which render fenced `mermaid` code blocks).

CORE PRINCIPLE: For any diagram that has nodes and edges, output a Mermaid diagram that
reproduces the EXACT structure — same nodes, same labels verbatim, same edges, same
arrow directions. Do NOT summarize a diagram in prose when Mermaid can encode it.

Pick the Mermaid dialect by diagram type:

| Image type                                               | Mermaid dialect          |
|----------------------------------------------------------|--------------------------|
| Architecture / system design / microservices / dataflow  | `flowchart LR` or `TB`   |
| Deployment / infrastructure with zones                   | `flowchart` + `subgraph` |
| Sequence / interaction diagrams                          | `sequenceDiagram`        |
| State machines                                           | `stateDiagram-v2`        |
| Class / component / UML structure                        | `classDiagram`           |
| Flowcharts / decision trees                              | `flowchart TD`           |
| Entity-relationship                                      | `erDiagram`              |
| Gantt / timeline                                         | `gantt`                  |

Mermaid authoring rules:
- Short alphanumeric node IDs (LB, Svc1, UserDB). Human-readable labels go in brackets:
  `Svc1["Order Service"]`. Quote labels that contain spaces or punctuation.
- Preserve arrow style from the source: solid `-->`, dashed `-.->`, thick `==>`,
  bidirectional as two edges.
- Put ALL text from the line onto the edge label: `A -->|HTTP POST /orders| B`.
  Include protocols (HTTP, gRPC, Kafka, SQL, S3), payload, cardinality (1..*, 0..n).
- Wrap deployment/trust boundaries (VPC, region, AZ, cluster, DMZ, process, public /
  private) as `subgraph "Boundary Name" ... end`.
- For sequence diagrams use `participant A as "Full Label"`, messages as `A->>B: text`
  (sync) or `A-->>B: text` (async/reply), notes as `Note over X: ...`. Preserve step
  ordering from top to bottom.
- For state diagrams use `[*] --> Initial`, `State1 --> State2 : trigger [guard] / action`.
- For flowcharts: decision diamonds as `Cond{"Question?"}`, yes/no as `|yes|` / `|no|`.
- Transcribe every label, legend item, and annotation verbatim — never rename or translate.
- If a diagram has numbered steps, add `%% Step N` comments on the edges.

Output structure for a diagram:

```mermaid
<dialect>
    <nodes>
    <edges>
```

(Optional — only if the diagram has a title or caption visible in the source:
one italic line after the block, e.g. `*Title: …*`.)

For image types that do NOT fit a diagram dialect, use these formats instead:

- Tables rendered as images → transcribe as a GitHub-flavored Markdown table, preserving
  column order and header hierarchy.
- Bar / line / pie / scatter charts → 3-6 bullet points: axes (labels + units), series
  names, trend direction, notable min/max values, inflection points.
- Screenshots / UI → verbatim text transcription first, then a bulleted list of
  affordances (buttons, tabs, menus, panels) and their spatial relationship.
- Photos / illustrations → 1-3 sentences on subject, setting, notable details.
- Infographics that mix text + icons without a clean graph structure → short bulleted
  hierarchy that preserves all callout text verbatim.

Global output rules:
- No preamble ("This image shows…", "The diagram depicts…"). Jump straight into the
  Mermaid block or the structured content.
- No explanatory commentary beyond what the image itself shows.
- Never truncate. If a diagram has 20 nodes, emit all 20.
- Output only Markdown-ready content — it will be inserted verbatim into the document."""


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------

def make_image_marker() -> str:
    """Generate a unique image placeholder marker."""
    return f"<!--IMG:{uuid4()}-->"


_MARKER_PATTERN = re.compile(r"<!--IMG:[0-9a-f\-]+-->")


def replace_image_markers(text: str, descriptions: Dict[str, str]) -> str:
    """Replace image position markers with formatted descriptions.

    The description body is emitted as-is so fenced ```mermaid blocks, Markdown
    tables, and multi-line bullet lists render correctly. A single ``*Image:*``
    italic label precedes the body so a reader can tell description from prose.
    """
    def _replace(match):
        marker = match.group(0)
        desc = descriptions.get(marker)
        if desc:
            body = desc.strip()
            return f"\n\n*Image:*\n\n{body}\n\n"
        return "\n\n*Image: description unavailable*\n\n"

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
        f"Describe this image for embedding in a Markdown document.\n"
        f"Context: {context}\n"
        f"If this is ANY kind of diagram with nodes and edges (architecture, sequence, "
        f"flow, state, class, ER, deployment), output a ```mermaid code block that "
        f"reproduces the structure exactly — same nodes, same labels verbatim, same "
        f"arrow directions and edge labels. Do not summarize a diagram in prose."
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
