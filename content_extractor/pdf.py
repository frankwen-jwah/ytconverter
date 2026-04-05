"""PDF content extraction via pymupdf4llm — layout-aware text, headings, sections."""

import pathlib
import re
import tempfile
from collections import Counter
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from .exceptions import PDFExtractionError
from .models import ArticleSection, ExtractedImage

if TYPE_CHECKING:
    from .config import PDFConfig

# Patterns that indicate mathematical content
_MATH_INDICATORS = re.compile(
    r"[\u2200-\u22FF\u2A00-\u2AFF\u27C0-\u27EF\u2190-\u21FF]"  # math symbols
    r"|\\(?:frac|sum|int|prod|lim|infty|partial|nabla|sqrt)\b"    # LaTeX commands
    r"|\(\d+\)\s*$"                                                # equation numbers
)

# Section headings that signal the references section
_REFERENCES_HEADINGS = re.compile(
    r"^(?:references|bibliography|works\s+cited|cited\s+works)$",
    re.IGNORECASE,
)


def extract_pdf_sections(
    pdf_bytes: bytes,
    config: "PDFConfig",
    extract_images: bool = False,
) -> Tuple[List[ArticleSection], Dict, bool, List[ExtractedImage]]:
    """Extract structured sections from PDF bytes using pymupdf4llm.

    Returns ``(sections, pdf_metadata, has_math, images)`` where
    *pdf_metadata* contains ``page_count``, ``word_count``, ``title``,
    ``author``, ``creation_date``, and *images* is a list of
    ``ExtractedImage`` objects (empty if *extract_images* is False).
    """
    from .deps import ensure_pymupdf4llm
    ensure_pymupdf4llm()

    import pymupdf
    import pymupdf4llm

    # Open PDF from bytes
    try:
        doc = pymupdf.Document(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PDFExtractionError(f"Failed to open PDF: {exc}") from exc

    page_count = len(doc)
    if page_count == 0:
        raise PDFExtractionError("PDF has no pages")

    # Extract document metadata
    meta = doc.metadata or {}
    pdf_metadata = {
        "page_count": page_count,
        "title": (meta.get("title") or "").strip(),
        "author": (meta.get("author") or "").strip(),
        "creation_date": _parse_pdf_date(meta.get("creationDate", "")),
    }

    # Determine page range
    page_chunks = None
    if config.max_pages > 0:
        page_chunks = list(range(min(config.max_pages, page_count)))

    # Use pymupdf4llm for layout-aware extraction
    # When extracting images, save them to a temp dir so we can read them
    image_dir = None
    _cleanup_dir = None  # Preserved for finally cleanup even if fallback nulls image_dir
    if extract_images:
        image_dir = tempfile.mkdtemp(prefix="pdf_images_")
        _cleanup_dir = image_dir

    try:
        extract_kwargs = dict(pages=page_chunks, show_progress=False)
        if image_dir:
            extract_kwargs["write_images"] = True
            extract_kwargs["image_path"] = image_dir

        try:
            md_text = pymupdf4llm.to_markdown(doc, **extract_kwargs)
        except Exception as exc:
            # Fallback: raw text extraction (no images in fallback)
            print(f"  WARNING: pymupdf4llm extraction failed ({exc}), "
                  "falling back to basic extraction...", flush=True)
            md_text = _fallback_extract(doc, page_chunks)
            image_dir = None  # No images from fallback path

        doc.close()

        if not md_text or len(md_text.strip()) < config.min_content_length:
            char_count = len(md_text.strip()) if md_text else 0
            raise PDFExtractionError(
                f"Very little text extracted ({char_count} chars from {page_count} pages). "
                "This PDF may contain scanned images. "
                "OCR support is planned for a future release."
            )

        # Extract images from markdown before section parsing
        images: List[ExtractedImage] = []
        if image_dir:
            md_text, images = _extract_images_from_markdown(md_text, image_dir)

        # Parse markdown into sections
        sections = _parse_markdown_to_sections(md_text)

        # Strip references if configured
        if config.strip_references:
            sections = _strip_references_section(sections)

        # Detect math
        full_text = "\n".join(s.body for s in sections)
        has_math = _detect_math(full_text)

        # Word count
        pdf_metadata["word_count"] = len(full_text.split())

        return sections, pdf_metadata, has_math, images

    finally:
        if _cleanup_dir:
            import shutil
            try:
                shutil.rmtree(_cleanup_dir)
            except (PermissionError, OSError):
                pass


def _extract_images_from_markdown(
    md_text: str,
    image_dir: str,
) -> Tuple[str, List[ExtractedImage]]:
    """Replace ``![alt](path)`` in pymupdf4llm output with markers.

    Reads image bytes from *image_dir*, creates ``ExtractedImage`` objects,
    and substitutes each image reference with a unique marker.
    Returns ``(modified_text, images)``.
    """
    from .vision import make_image_marker

    images: List[ExtractedImage] = []
    base = pathlib.Path(image_dir)

    def _replace(match):
        alt = match.group(1)
        ref = match.group(2)
        # pymupdf4llm writes paths relative to image_path or as data URIs
        if ref.startswith("data:"):
            return ""  # Skip inline data URIs
        fpath = base / ref if not pathlib.Path(ref).is_absolute() else pathlib.Path(ref)
        if not fpath.exists():
            return ""
        try:
            image_bytes = fpath.read_bytes()
            if len(image_bytes) < 100:  # Skip degenerate tiny files
                return ""
            marker = make_image_marker()
            ext = fpath.suffix.lstrip(".").lower()
            fmt = ext if ext in ("png", "jpeg", "jpg") else "png"
            images.append(ExtractedImage(
                image_bytes=image_bytes,
                format=fmt,
                source_label="PDF figure",
                position_marker=marker,
                alt_text=alt,
            ))
            return marker
        except OSError:
            return ""

    modified = re.sub(r"!\[(.*?)\]\((.*?)\)", _replace, md_text)
    return modified, images


def extract_abstract(
    sections: List[ArticleSection],
) -> Tuple[str, List[ArticleSection]]:
    """Pull the abstract out of sections for frontmatter.

    Returns ``(abstract_text, remaining_sections)``.
    If no abstract heading is found, returns ``("", sections)``.
    """
    for i, section in enumerate(sections):
        if re.match(r"^abstract$", section.heading.strip(), re.IGNORECASE):
            abstract = section.body.strip()
            remaining = sections[:i] + sections[i + 1:]
            return abstract, remaining
    return "", list(sections)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def parse_markdown_to_sections(md_text: str,
                               pdf_cleanup: bool = True) -> List[ArticleSection]:
    """Parse Markdown text into ``ArticleSection`` objects.

    Public API — also used by the local-file extraction pipeline.
    When *pdf_cleanup* is False, PDF-specific body cleaning (image placeholder
    removal, hyphenated line-break merging) is skipped.
    """
    return _parse_markdown_to_sections(md_text, pdf_cleanup=pdf_cleanup)


def _parse_markdown_to_sections(md_text: str,
                                pdf_cleanup: bool = True) -> List[ArticleSection]:
    """Parse pymupdf4llm Markdown output into ``ArticleSection`` objects."""
    lines = md_text.split("\n")
    sections: List[ArticleSection] = []
    current_heading = ""
    current_level = 2
    current_body_lines: List[str] = []

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            # Flush previous section
            body = _clean_body("\n".join(current_body_lines), pdf_cleanup)
            if body or current_heading:
                sections.append(ArticleSection(
                    heading=current_heading,
                    level=current_level,
                    body=body,
                ))
            current_heading = heading_match.group(2).strip()
            current_level = len(heading_match.group(1))
            current_body_lines = []
        else:
            current_body_lines.append(line)

    # Flush last section
    body = _clean_body("\n".join(current_body_lines), pdf_cleanup)
    if body or current_heading:
        sections.append(ArticleSection(
            heading=current_heading,
            level=current_level,
            body=body,
        ))

    return _refine_heading_levels(sections)


def _clean_body(text: str, pdf_cleanup: bool = True) -> str:
    """Clean up body text — collapse excessive blank lines, strip edges.

    When *pdf_cleanup* is True (default), also remove image placeholders and
    merge hyphenated line breaks — transformations specific to PDF output.
    """
    text = text.strip()
    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    if pdf_cleanup:
        # Remove image placeholders that pymupdf4llm may insert
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        # Merge hyphenated line breaks
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return text.strip()


def _refine_heading_levels(sections: List[ArticleSection]) -> List[ArticleSection]:
    """Normalise heading levels so they start at 2 (h1 is reserved for title).

    pymupdf4llm sometimes assigns all headings the same level or uses h1
    for section headings.  This shifts levels so the top heading is h2.
    """
    if not sections:
        return sections

    heading_sections = [s for s in sections if s.heading]
    if not heading_sections:
        return sections

    min_level = min(s.level for s in heading_sections)
    if min_level >= 2:
        return sections  # already fine

    shift = 2 - min_level
    return [
        ArticleSection(
            heading=s.heading,
            level=s.level + shift if s.heading else s.level,
            body=s.body,
        )
        for s in sections
    ]


def _strip_references_section(
    sections: List[ArticleSection],
) -> List[ArticleSection]:
    """Remove the References/Bibliography section and everything after it."""
    for i, section in enumerate(sections):
        if _REFERENCES_HEADINGS.match(section.heading.strip()):
            return sections[:i]
    return sections


def _detect_math(text: str) -> bool:
    """Return True if text contains mathematical notation indicators."""
    return bool(_MATH_INDICATORS.search(text))


def _parse_pdf_date(raw: str) -> str:
    """Parse PDF metadata date (D:YYYYMMDDHHmmSS) to YYYY-MM-DD."""
    if not raw:
        return "unknown"
    # Strip D: prefix
    raw = raw.strip()
    if raw.startswith("D:"):
        raw = raw[2:]
    # Extract YYYYMMDD
    match = re.match(r"(\d{4})(\d{2})(\d{2})", raw)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return "unknown"


def _fallback_extract(doc, pages: Optional[List[int]] = None) -> str:
    """Basic text extraction when pymupdf4llm fails.

    Uses pymupdf's get_text with sort=True for reading-order text.
    """
    parts = []
    page_range = pages if pages is not None else range(len(doc))
    for page_num in page_range:
        page = doc[page_num]
        text = page.get_text("text", sort=True)
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)
