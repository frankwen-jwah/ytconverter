"""Article content extraction — parse HTML into structured sections via trafilatura."""

import re
from datetime import datetime
from typing import List, Optional, Tuple, TYPE_CHECKING
from xml.etree import ElementTree

from .exceptions import ContentExtractionError
from .models import ArticleInfo, ArticleSection, ExtractedImage

if TYPE_CHECKING:
    from .config import ArticlesConfig


# ---------------------------------------------------------------------------
# Trafilatura XML → ArticleSection list
# ---------------------------------------------------------------------------

def _parse_trafilatura_xml(
    xml_str: str,
    extract_images: bool = False,
) -> Tuple[List[ArticleSection], List[Tuple[str, str, str]]]:
    """Parse trafilatura XML output, preserving heading structure.

    trafilatura XML uses ``<head rend="h2">`` for headings, ``<p>`` for
    paragraphs, and ``<graphic>`` for images.  We group consecutive elements
    under the nearest preceding ``<head>`` to produce ``ArticleSection``
    objects.

    Returns ``(sections, pending_images)`` where *pending_images* is a list
    of ``(url, alt_text, marker)`` tuples for images that need downloading.
    Empty if *extract_images* is False.
    """
    try:
        root = ElementTree.fromstring(xml_str)
    except ElementTree.ParseError:
        return [], []

    sections: List[ArticleSection] = []
    pending_images: List[Tuple[str, str, str]] = []
    current_heading = ""
    current_level = 2
    current_paragraphs: List[str] = []

    def _flush():
        if current_paragraphs:
            body = "\n\n".join(current_paragraphs)
            sections.append(ArticleSection(
                heading=current_heading,
                level=current_level,
                body=body,
            ))

    # Walk all elements in document order
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        if tag == "head":
            _flush()
            current_heading = (elem.text or "").strip()
            rend = elem.get("rend", "h2")
            m = re.search(r"(\d+)", rend)
            current_level = int(m.group(1)) if m else 2
            current_paragraphs = []

        elif tag == "p":
            text = "".join(elem.itertext()).strip()
            if text:
                current_paragraphs.append(text)

        elif tag == "graphic" and extract_images:
            src = elem.get("src", "")
            alt = elem.get("title", "") or elem.get("alt", "")
            if src and src.startswith("http"):
                from .vision import make_image_marker
                marker = make_image_marker()
                pending_images.append((src, alt, marker))
                current_paragraphs.append(marker)

    _flush()
    return sections, pending_images


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def _extract_metadata(html: str, url: str) -> dict:
    """Extract article metadata using trafilatura."""
    import trafilatura

    meta = trafilatura.extract_metadata(html, default_url=url)
    if meta is None:
        return {"title": "", "author": "", "sitename": "",
                "date": None, "description": "", "language": None}

    return {
        "title": meta.title or "",
        "author": meta.author or "",
        "sitename": meta.sitename or "",
        "date": meta.date,
        "description": meta.description or "",
        "language": getattr(meta, "language", None),
    }


def _normalise_date(raw_date: Optional[str]) -> str:
    """Return YYYY-MM-DD from various date formats, or ``"unknown"``."""
    if not raw_date:
        return "unknown"
    # Already YYYY-MM-DD?
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
        return raw_date
    # Try common formats
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw_date[:10] if len(raw_date) >= 10 else "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sections_to_body_text(sections: List[ArticleSection]) -> str:
    """Join sections into a single body string for LLM consumption."""
    parts = []
    for sec in sections:
        if sec.heading:
            parts.append(sec.heading)
        parts.append(sec.body)
    return "\n\n".join(parts)


def extract_article(
    html: str,
    url: str,
    config: "ArticlesConfig",
    extract_images: bool = False,
    verify_ssl: bool = True,
) -> Tuple[ArticleInfo, List[ArticleSection], List[ExtractedImage]]:
    """Extract article content and metadata from HTML.

    Returns ``(ArticleInfo, sections, images)`` where *images* is a list
    of ``ExtractedImage`` objects (empty if *extract_images* is False).
    Raises ``ContentExtractionError`` if no meaningful content found.
    """
    from .deps import ensure_trafilatura
    ensure_trafilatura()
    import trafilatura

    # When extracting images, force include_images for trafilatura
    include_images = True if extract_images else config.include_images

    # 1. Structured extraction (XML preserves headings)
    xml_result = trafilatura.extract(
        html,
        output_format="xml",
        include_tables=config.include_tables,
        include_links=config.include_links,
        include_images=include_images,
    )

    sections: List[ArticleSection] = []
    pending_images: List[Tuple[str, str, str]] = []
    if xml_result:
        sections, pending_images = _parse_trafilatura_xml(
            xml_result, extract_images=extract_images)

    # 2. Fallback: plain text extraction → single flat section
    if not sections:
        plain = trafilatura.extract(
            html,
            include_tables=config.include_tables,
            include_links=config.include_links,
            include_images=include_images,
        )
        if plain and plain.strip():
            sections = [ArticleSection(heading="", level=2, body=plain.strip())]

    if not sections:
        raise ContentExtractionError("Could not extract any content from the page.")

    # 3. Download pending images
    images: List[ExtractedImage] = []
    if pending_images:
        from .http_fetch import fetch_image_bytes
        for img_url, alt_text, marker in pending_images:
            img_bytes = fetch_image_bytes(img_url, verify_ssl=verify_ssl)
            if img_bytes:
                ext = "jpeg" if ".jpg" in img_url or ".jpeg" in img_url else "png"
                images.append(ExtractedImage(
                    image_bytes=img_bytes,
                    format=ext,
                    source_label="Article figure",
                    position_marker=marker,
                    alt_text=alt_text,
                ))

    # 4. Metadata
    meta = _extract_metadata(html, url)
    body_text = sections_to_body_text(sections)
    word_count = len(body_text.split())

    if word_count < max(1, config.min_content_length // 5):
        raise ContentExtractionError(
            f"Extracted content too short ({word_count} words)."
        )

    info = ArticleInfo(
        title=meta["title"] or _guess_title(sections),
        url=url,
        author=meta["author"],
        site_name=meta["sitename"],
        publish_date=_normalise_date(meta["date"]),
        language=meta["language"],
        description=meta["description"],
        word_count=word_count,
        sections=sections,
    )

    return info, sections, images


def _guess_title(sections: List[ArticleSection]) -> str:
    """Derive a title from the first heading if metadata had none."""
    for sec in sections:
        if sec.heading:
            return sec.heading
    return "Untitled"
