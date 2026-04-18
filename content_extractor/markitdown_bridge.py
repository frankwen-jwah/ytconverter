"""MarkItDown integration — unified file-to-Markdown converter."""

import logging
import os
import pathlib
from datetime import datetime
from typing import List, Tuple, TYPE_CHECKING

from .exceptions import MarkItDownError
from .models import ArticleInfo, ArticleSection

if TYPE_CHECKING:
    from .config import Config, MarkItDownConfig

_log = logging.getLogger("content_extractor.markitdown_bridge")

# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_md_instance = None


def _get_markitdown(config: "Config"):
    """Return a cached MarkItDown instance, creating on first call.

    When config.markitdown.llm_enabled is True, passes the Azure OpenAI
    client from llm_backend for image description features.
    """
    global _md_instance
    if _md_instance is not None:
        return _md_instance

    from .deps import ensure_markitdown
    ensure_markitdown()

    from markitdown import MarkItDown

    llm_client = None
    llm_model = None
    if config.markitdown.llm_enabled:
        try:
            from . import llm_backend
            llm_client = llm_backend.get_client()
            llm_model = (config.markitdown.llm_model
                         or llm_backend.get_deployment())
        except Exception as e:
            _log.warning("Could not get LLM client for MarkItDown: %s", e)

    _md_instance = MarkItDown(
        llm_client=llm_client,
        llm_model=llm_model,
    )
    return _md_instance


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

def _parse_markdown_to_sections(md_text: str) -> List[ArticleSection]:
    """Parse Markdown text into ArticleSection list, splitting on ## headings."""
    lines = md_text.split("\n")
    sections: List[ArticleSection] = []
    current_title = ""
    current_body: List[str] = []
    preamble: List[str] = []
    in_preamble = True

    for line in lines:
        if line.startswith("## "):
            if in_preamble:
                in_preamble = False
                if preamble:
                    body = "\n".join(preamble).strip()
                    if body:
                        sections.append(ArticleSection(heading="", level=2, body=body))
            else:
                body = "\n".join(current_body).strip()
                sections.append(ArticleSection(heading=current_title, level=2, body=body))
            current_title = line[3:].strip()
            current_body = []
        elif line.startswith("# ") and in_preamble:
            # Top-level heading — skip (used for title)
            continue
        elif in_preamble:
            preamble.append(line)
        else:
            current_body.append(line)

    # Flush last section
    if in_preamble:
        body = "\n".join(preamble).strip()
        if body:
            sections.append(ArticleSection(heading="", level=2, body=body))
    else:
        body = "\n".join(current_body).strip()
        sections.append(ArticleSection(heading=current_title, level=2, body=body))

    return [s for s in sections if s.body]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_file(
    path: str,
    config: "Config",
) -> Tuple[ArticleInfo, List[ArticleSection]]:
    """Convert any file via MarkItDown, returning our data model.

    Returns (ArticleInfo, List[ArticleSection]).
    Raises MarkItDownError on failure.
    """
    p = pathlib.Path(path)
    if not p.exists():
        raise MarkItDownError(f"File not found: {path}")

    md = _get_markitdown(config)

    try:
        result = md.convert(str(p))
    except Exception as e:
        raise MarkItDownError(f"MarkItDown conversion failed for {p.name}: {e}") from e

    text = result.text_content or ""
    if not text.strip():
        raise MarkItDownError(f"MarkItDown produced empty output for {p.name}")

    # Parse into sections
    sections = _parse_markdown_to_sections(text)

    # Build ArticleInfo
    title = result.title or p.stem
    stat = p.stat()
    date_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
    word_count = len(text.split())

    info = ArticleInfo(
        title=title,
        url=str(p),
        author="",
        site_name="",
        publish_date=date_str,
        language=None,
        description="",
        word_count=word_count,
        sections=sections,
    )

    return info, sections


