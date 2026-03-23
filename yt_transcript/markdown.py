"""Markdown generation — assemble final output document."""

from .models import TranscriptResult
from .text import align_cues_to_chapters, cues_to_text


def escape_yaml_string(s: str) -> str:
    """Escape a string for YAML double-quoted value."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_markdown(result: TranscriptResult, include_description: bool,
                   use_chapters: bool) -> str:
    """Assemble the final Markdown document."""
    info = result.info
    lines = []

    # YAML frontmatter
    lines.append("---")
    lines.append(f'title: "{escape_yaml_string(info.title)}"')
    lines.append(f'url: "{info.url}"')
    lines.append(f'channel: "{escape_yaml_string(info.channel)}"')
    lines.append(f'date: "{info.upload_date}"')
    lines.append(f'language: "{result.sub_language}"')
    lines.append(f'duration: "{info.duration_string}"')
    lines.append(f"auto_generated: {str(result.is_auto_generated).lower()}")
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# {info.title}")
    lines.append("")

    # Metadata line
    lines.append(f"> Channel: {info.channel} | Date: {info.upload_date} | Duration: {info.duration_string}")
    lines.append("")

    # Auto-generated warning
    if result.is_auto_generated:
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
            text = cues_to_text(chapter_cues.get(i, []))
            if text:
                lines.append(text)
            else:
                lines.append("*(No transcript for this section)*")
            lines.append("")
    else:
        text = cues_to_text(result.cues)
        if text:
            lines.append(text)
        else:
            lines.append("*(No transcript text extracted)*")
        lines.append("")

    return "\n".join(lines)
