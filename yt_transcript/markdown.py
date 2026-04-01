"""Markdown generation — assemble final output document."""

from typing import List, Optional, Tuple, TYPE_CHECKING

from .models import ArticleResult, PDFResult, PodcastResult, TranscriptResult, TweetResult
from .text import align_cues_to_chapters, cues_to_text

if TYPE_CHECKING:
    from .config import TextConfig


def escape_yaml_string(s: str) -> str:
    """Escape a string for YAML double-quoted value."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Shared frontmatter helper (DRY #2)
# ---------------------------------------------------------------------------

def _render_frontmatter(fields: List[Tuple[str, object]]) -> str:
    """Render a YAML frontmatter block from an ordered list of (key, value) pairs.

    String values are double-quoted and escaped.  Booleans are lowercased.
    ``None`` values are skipped.
    """
    lines = ["---"]
    for key, val in fields:
        if val is None:
            continue
        if isinstance(val, bool):
            lines.append(f"{key}: {str(val).lower()}")
        elif isinstance(val, (int, float)):
            lines.append(f"{key}: {val}")
        else:
            lines.append(f'{key}: "{escape_yaml_string(str(val))}"')
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# YouTube transcript markdown
# ---------------------------------------------------------------------------

def build_markdown(result: TranscriptResult, include_description: bool,
                   use_chapters: bool, polished: bool = False,
                   text_config: Optional["TextConfig"] = None) -> str:
    """Assemble the final Markdown document for a YouTube transcript."""
    info = result.info
    lines = []

    # Text config kwargs for cues_to_text
    text_kw = {}
    if text_config:
        text_kw = {
            "paragraph_gap": text_config.paragraph_gap_seconds,
            "sentence_break": text_config.sentence_break_count,
            "cjk_threshold": text_config.cjk_threshold,
        }

    # YAML frontmatter
    fm_fields: List[Tuple[str, object]] = [
        ("title", info.title),
        ("url", info.url),
        ("channel", info.channel),
        ("date", info.upload_date),
        ("language", result.sub_language),
        ("duration", info.duration_string),
        ("auto_generated", result.is_auto_generated),
    ]
    if result.is_whisper_transcribed:
        fm_fields.append(("whisper_transcribed", True))
    fm_fields.append(("content_type", "transcript"))
    fm_fields.append(("polished", polished))
    lines.append(_render_frontmatter(fm_fields))
    lines.append("")

    # Title
    lines.append(f"# {info.title}")
    lines.append("")

    # Metadata line
    lines.append(f"> Channel: {info.channel} | Date: {info.upload_date} | Duration: {info.duration_string}")
    lines.append("")

    # Source warning
    if result.is_whisper_transcribed:
        lines.append("*Transcribed from audio using Whisper — may contain errors.*")
        lines.append("")
    elif result.is_auto_generated:
        lines.append("*Auto-generated transcript — may contain errors.*")
        lines.append("")

    # Description (collapsible)
    if include_description and info.description:
        lines.append("<details>")
        lines.append("<summary>Video Description</summary>")
        lines.append("")
        lines.append(info.description)
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Transcript body
    if use_chapters and info.chapters:
        chapter_cues = align_cues_to_chapters(result.cues, info.chapters)
        for i, chapter in enumerate(info.chapters):
            lines.append(f"## {chapter.title}")
            lines.append("")
            text = cues_to_text(chapter_cues.get(i, []), **text_kw)
            if text:
                lines.append(text)
            else:
                lines.append("*(No transcript for this section)*")
            lines.append("")
    else:
        text = cues_to_text(result.cues, **text_kw)
        if text:
            lines.append(text)
        else:
            lines.append("*(No transcript text extracted)*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Article markdown
# ---------------------------------------------------------------------------

def build_article_markdown(result: ArticleResult,
                           include_description: bool = False,
                           polished: bool = False,
                           content_type: str = "article") -> str:
    """Assemble the final Markdown document for a web article or local file."""
    info = result.info
    lines = []

    # YAML frontmatter (shared helper)
    fm_fields: List[Tuple[str, object]] = [
        ("title", info.title),
        ("url", info.url),
        ("author", info.author or None),
        ("site_name", info.site_name or None),
        ("date", info.publish_date),
        ("language", info.language),
        ("word_count", info.word_count),
        ("content_type", content_type),
        ("polished", polished),
    ]
    lines.append(_render_frontmatter(fm_fields))
    lines.append("")

    # Title
    lines.append(f"# {info.title}")
    lines.append("")

    # Metadata line
    meta_parts = []
    if info.author:
        meta_parts.append(f"Author: {info.author}")
    if info.site_name:
        meta_parts.append(f"Site: {info.site_name}")
    meta_parts.append(f"Date: {info.publish_date}")
    meta_parts.append(f"{info.word_count} words")
    lines.append(f"> {' | '.join(meta_parts)}")
    lines.append("")

    # Description (collapsible)
    if include_description and info.description:
        lines.append("<details>")
        lines.append("<summary>Article Description</summary>")
        lines.append("")
        lines.append(info.description)
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Article body — sections with headings
    for section in result.sections:
        if section.heading:
            prefix = "#" * min(section.level, 6)
            lines.append(f"{prefix} {section.heading}")
            lines.append("")
        if section.body:
            lines.append(section.body)
        else:
            lines.append("*(No content for this section)*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PDF paper markdown
# ---------------------------------------------------------------------------

def build_pdf_markdown(result: PDFResult,
                       include_abstract: bool = True,
                       polished: bool = False) -> str:
    """Assemble the final Markdown document for a PDF paper."""
    info = result.info
    lines = []

    # YAML frontmatter
    authors_str = ", ".join(info.authors) if info.authors else None
    categories_str = ", ".join(info.categories) if info.categories else None

    fm_fields: List[Tuple[str, object]] = [
        ("title", info.title),
        ("url", info.url),
        ("pdf_url", info.pdf_url),
        ("authors", authors_str),
        ("date", info.publish_date),
        ("arxiv_id", info.arxiv_id),
        ("doi", info.doi),
        ("categories", categories_str),
        ("language", info.language),
        ("pages", info.page_count),
        ("word_count", info.word_count),
        ("content_type", "paper"),
        ("has_math", result.has_math),
        ("polished", polished),
    ]
    lines.append(_render_frontmatter(fm_fields))
    lines.append("")

    # Title
    lines.append(f"# {info.title}")
    lines.append("")

    # Metadata line
    meta_parts = []
    if info.authors:
        author_display = ", ".join(info.authors[:3])
        if len(info.authors) > 3:
            author_display += f" et al. ({len(info.authors)} authors)"
        meta_parts.append(f"Authors: {author_display}")
    meta_parts.append(f"Date: {info.publish_date}")
    if info.arxiv_id:
        meta_parts.append(f"arXiv: {info.arxiv_id}")
    meta_parts.append(f"{info.page_count} pages | {info.word_count} words")
    lines.append(f"> {' | '.join(meta_parts)}")
    lines.append("")

    # Math warning
    if result.has_math:
        lines.append("*This paper contains mathematical notation that may not "
                      "render perfectly in extracted text.*")
        lines.append("")

    # Abstract
    if include_abstract and info.abstract:
        lines.append("## Abstract")
        lines.append("")
        lines.append(info.abstract)
        lines.append("")

    # Body sections (same rendering as articles)
    for section in result.sections:
        if section.heading:
            prefix = "#" * min(section.level, 6)
            lines.append(f"{prefix} {section.heading}")
            lines.append("")
        if section.body:
            lines.append(section.body)
        else:
            lines.append("*(No content for this section)*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tweet/thread markdown
# ---------------------------------------------------------------------------

def build_tweet_markdown(result: TweetResult,
                         polished: bool = False) -> str:
    """Assemble the final Markdown document for a tweet or thread."""
    info = result.info
    lines = []

    # YAML frontmatter
    fm_fields: List[Tuple[str, object]] = [
        ("title", info.title),
        ("url", info.url),
        ("author", info.author),
        ("author_name", info.author_name),
        ("date", info.publish_date),
        ("word_count", info.word_count),
        ("is_thread", info.is_thread),
        ("thread_length", info.thread_length),
        ("tweet_subtype", info.tweet_subtype
         if info.tweet_subtype != "tweet" else None),
        ("content_type", "tweet"),
        ("polished", polished),
    ]
    lines.append(_render_frontmatter(fm_fields))
    lines.append("")

    # Title
    lines.append(f"# {info.title}")
    lines.append("")

    # Metadata line
    meta_parts = [f"Author: {info.author} ({info.author_name})"]
    meta_parts.append(f"Date: {info.publish_date}")
    meta_parts.append(f"{info.word_count} words")
    lines.append(f"> {' | '.join(meta_parts)}")
    lines.append("")

    # Thread indicator
    if info.is_thread:
        lines.append(f"*Thread ({info.thread_length} posts)*")
        lines.append("")

    # Body — sections (same rendering as articles)
    for section in result.sections:
        if section.heading:
            prefix = "#" * min(section.level, 6)
            lines.append(f"{prefix} {section.heading}")
            lines.append("")
        if section.body:
            lines.append(section.body)
        else:
            lines.append("*(No content)*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Podcast episode markdown
# ---------------------------------------------------------------------------

def build_podcast_markdown(result: PodcastResult,
                           include_description: bool = False,
                           polished: bool = False,
                           text_config: Optional["TextConfig"] = None) -> str:
    """Assemble the final Markdown document for a podcast episode transcript."""
    info = result.info
    lines = []

    # Text config kwargs for cues_to_text
    text_kw = {}
    if text_config:
        text_kw = {
            "paragraph_gap": text_config.paragraph_gap_seconds,
            "sentence_break": text_config.sentence_break_count,
            "cjk_threshold": text_config.cjk_threshold,
        }

    # YAML frontmatter
    fm_fields: List[Tuple[str, object]] = [
        ("title", info.title),
        ("show_name", info.show_name),
        ("episode_number", info.episode_number),
        ("url", info.url),
        ("date", info.publish_date),
        ("duration", info.duration_string),
        ("language", info.language),
        ("whisper_transcribed", True),
        ("content_type", "podcast"),
        ("polished", polished),
    ]
    lines.append(_render_frontmatter(fm_fields))
    lines.append("")

    # Title
    lines.append(f"# {info.title}")
    lines.append("")

    # Metadata line
    meta_parts = []
    if info.show_name:
        meta_parts.append(f"Show: {info.show_name}")
    if info.episode_number:
        meta_parts.append(f"Episode: {info.episode_number}")
    meta_parts.append(f"Date: {info.publish_date}")
    meta_parts.append(f"Duration: {info.duration_string}")
    lines.append(f"> {' | '.join(meta_parts)}")
    lines.append("")

    # Whisper warning (always present for podcasts)
    lines.append("*Transcribed from audio using Whisper — may contain errors.*")
    lines.append("")

    # Description (collapsible)
    if include_description and info.description:
        lines.append("<details>")
        lines.append("<summary>Episode Description</summary>")
        lines.append("")
        lines.append(info.description)
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Transcript body (flat — no chapters for podcasts)
    text = cues_to_text(result.cues, **text_kw)
    if text:
        lines.append(text)
    else:
        lines.append("*(No transcript text extracted)*")
    lines.append("")

    return "\n".join(lines)
