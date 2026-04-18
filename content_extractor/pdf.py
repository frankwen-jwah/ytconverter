"""PDF content extraction via opendataloader-pdf — layout-aware text, headings, sections."""

import json
import os
import pathlib
import re
import tempfile
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
    """Extract structured sections from PDF bytes using opendataloader-pdf.

    Returns ``(sections, pdf_metadata, has_math, images)`` where
    *pdf_metadata* contains ``page_count``, ``word_count``, ``title``,
    ``author``, ``creation_date``, and *images* is a list of
    ``ExtractedImage`` objects (empty if *extract_images* is False).
    """
    from .deps import ensure_opendataloader_pdf
    ensure_opendataloader_pdf()

    import opendataloader_pdf

    work_dir = tempfile.mkdtemp(prefix="odl_pdf_")
    try:
        # Write PDF bytes to temp file (opendataloader requires file path)
        pdf_path = os.path.join(work_dir, "input.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        output_dir = os.path.join(work_dir, "output")
        os.makedirs(output_dir)

        # Build image directory
        image_dir = None
        if extract_images:
            image_dir = os.path.join(work_dir, "images")
            os.makedirs(image_dir)

        # Build pages parameter: "0,1,...,N-1" or None for all
        pages_param = None
        if config.max_pages > 0:
            pages_param = ",".join(str(i) for i in range(config.max_pages))

        # Build convert kwargs
        convert_kwargs = dict(
            input_path=pdf_path,
            output_dir=output_dir,
            format="markdown,json",
            table_method=config.table_method,
            reading_order=config.reading_order,
            use_struct_tree=config.use_struct_tree,
            include_header_footer=config.include_header_footer,
            quiet=True,
        )
        if pages_param:
            convert_kwargs["pages"] = pages_param
        if image_dir:
            convert_kwargs["image_output"] = "external"
            convert_kwargs["image_format"] = config.image_format
            convert_kwargs["image_dir"] = image_dir
        else:
            convert_kwargs["image_output"] = "off"

        # Run conversion
        print("  [pdf] Running opendataloader-pdf conversion...", flush=True)
        try:
            exit_code = opendataloader_pdf.convert(**convert_kwargs)
        except Exception as exc:
            raise PDFExtractionError(
                f"opendataloader-pdf conversion failed: {exc}") from exc

        # convert() returns None on success in current opendataloader-pdf versions.
        # Treat only explicit non-zero int as failure; trust the md_path check below.
        if isinstance(exit_code, int) and exit_code != 0:
            raise PDFExtractionError(
                f"opendataloader-pdf conversion failed (exit code {exit_code})")

        # Read markdown output (filename derived from input)
        md_path = os.path.join(output_dir, "input.md")
        if not os.path.exists(md_path):
            raise PDFExtractionError(
                "opendataloader-pdf produced no markdown output")

        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()

        # Extract metadata from JSON output
        json_path = os.path.join(output_dir, "input.json")
        pdf_metadata = _extract_metadata_from_json(json_path)
        print(f"  [pdf] Converted: {pdf_metadata['page_count']} pages", flush=True)

        # Validate content length
        if not md_text or len(md_text.strip()) < config.min_content_length:
            char_count = len(md_text.strip()) if md_text else 0
            raise PDFExtractionError(
                f"Very little text extracted ({char_count} chars from "
                f"{pdf_metadata.get('page_count', '?')} pages). "
                "This PDF may contain scanned images — try enabling OCR."
            )

        # Extract images from markdown before section parsing
        images: List[ExtractedImage] = []
        if image_dir:
            md_text, images = _extract_images_from_markdown(md_text, image_dir)
            if images:
                print(f"  [pdf] Extracted {len(images)} image(s)", flush=True)

        # Parse markdown into sections
        sections = _parse_markdown_to_sections(md_text)
        print(f"  [pdf] Parsed {len(sections)} section(s)", flush=True)

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
        import shutil
        try:
            shutil.rmtree(work_dir)
        except (PermissionError, OSError):
            pass


def _extract_images_from_markdown(
    md_text: str,
    image_dir: str,
) -> Tuple[str, List[ExtractedImage]]:
    """Replace ``![alt](path)`` in PDF markdown output with markers.

    Reads image bytes from *image_dir*, creates ``ExtractedImage`` objects,
    and substitutes each image reference with a unique marker.
    Returns ``(modified_text, images)``.
    """
    from .vision import make_image_marker

    images: List[ExtractedImage] = []
    base = pathlib.Path(image_dir)
    # opendataloader-pdf may emit image refs relative to the markdown file's
    # directory (output/), to work_dir, or as bare filenames — try each.
    search_roots = [base, base.parent, base.parent / "output"]
    counter = {"n": 0}

    def _resolve(ref: str) -> Optional[pathlib.Path]:
        ref_path = pathlib.Path(ref)
        if ref_path.is_absolute():
            return ref_path if ref_path.exists() else None
        for root in search_roots:
            cand = root / ref
            if cand.exists():
                return cand
        # Last resort: match by basename anywhere under image_dir
        return next(iter(base.rglob(ref_path.name)), None)

    def _replace(match):
        alt = match.group(1)
        ref = match.group(2)
        if ref.startswith("data:"):
            return ""  # Skip inline data URIs
        fpath = _resolve(ref)
        if fpath is None:
            return ""
        try:
            image_bytes = fpath.read_bytes()
            if len(image_bytes) < 100:  # Skip degenerate tiny files
                return ""
            marker = make_image_marker()
            ext = fpath.suffix.lstrip(".").lower()
            fmt = ext if ext in ("png", "jpeg", "jpg") else "png"
            counter["n"] += 1
            images.append(ExtractedImage(
                image_bytes=image_bytes,
                format=fmt,
                source_label=f"PDF figure {counter['n']}",
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
    """Parse PDF markdown output into ``ArticleSection`` objects."""
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
        # Remove residual image placeholders from PDF extraction
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        # Merge hyphenated line breaks
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return text.strip()


def _refine_heading_levels(sections: List[ArticleSection]) -> List[ArticleSection]:
    """Normalise heading levels so they start at 2 (h1 is reserved for title).

    PDF extractors sometimes assign all headings the same level or use h1
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


def _extract_metadata_from_json(json_path: str) -> Dict:
    """Extract PDF metadata from opendataloader-pdf JSON output."""
    metadata: Dict = {
        "page_count": 0,
        "title": "",
        "author": "",
        "creation_date": "unknown",
    }
    if not os.path.exists(json_path):
        return metadata
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        metadata["page_count"] = data.get("number_of_pages", 0)
        metadata["title"] = (data.get("title") or "").strip()
        metadata["author"] = (data.get("author") or "").strip()
        raw_date = data.get("creation_date", "")
        if raw_date:
            metadata["creation_date"] = _parse_pdf_date(raw_date)
    except (json.JSONDecodeError, KeyError, OSError):
        pass
    return metadata
