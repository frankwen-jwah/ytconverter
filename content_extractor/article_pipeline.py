"""Pipeline orchestration — single article processing and dry-run."""

from typing import TYPE_CHECKING

from .article import extract_article, sections_to_body_text
from .exceptions import PipelineError
from .http_fetch import fetch_html
from .models import ArticleResult

if TYPE_CHECKING:
    from .config import Config


def process_single_article(url: str, config: "Config") -> ArticleResult:
    """Full extraction pipeline for one article URL."""
    # 1. Fetch HTML
    html = fetch_html(url, config.articles, config.network)

    # 2. Extract content and metadata
    info, sections, images = extract_article(
        html, url, config.articles,
        extract_images=config.vision.enabled,
        verify_ssl=config.articles.verify_ssl)
    print(f"{info.title}", flush=True)

    # 2b. Describe images via Azure OpenAI vision
    if config.vision.enabled and images:
        from .vision import describe_images, replace_image_markers
        print(f"  [article] Describing {len(images)} image(s)...", flush=True)
        descriptions = describe_images(images, config)

        # Replace inline markers (trafilatura path — markers in section text)
        replaced_any = False
        for s in sections:
            new_body = replace_image_markers(s.body, descriptions)
            if new_body != s.body:
                s.body = new_body
                replaced_any = True

        # HTML fallback images: no inline markers. Use preceding-text
        # anchors to insert descriptions at original positions.
        if not replaced_any and descriptions:
            placed = set()
            anchor_offsets = {}  # (section_id, anchor) → next search pos
            for idx, img in enumerate(images):
                desc = descriptions.get(img.position_marker, "")
                if not desc or not img.alt_text:
                    continue
                formatted = f"\n\n> [Image: {desc.strip()}]\n"
                anchor = img.alt_text
                for s in sections:
                    key = (id(s), anchor)
                    offset = anchor_offsets.get(key, 0)
                    pos = s.body.find(anchor, offset)
                    if pos >= 0:
                        insert_at = pos + len(anchor)
                        s.body = (s.body[:insert_at] + formatted
                                  + s.body[insert_at:])
                        anchor_offsets[key] = insert_at + len(formatted)
                        placed.add(idx)
                        break

            # Append any unplaced descriptions at the end
            unplaced = []
            for idx, img in enumerate(images):
                if idx in placed:
                    continue
                desc = descriptions.get(img.position_marker, "")
                if desc:
                    unplaced.append(f"> [Image: {desc.strip()}]")
            if unplaced:
                from .models import ArticleSection
                sections.append(ArticleSection(
                    heading="Figures", level=2,
                    body="\n\n".join(unplaced),
                ))

    # 3. Assemble body text
    body_text = sections_to_body_text(sections)

    return ArticleResult(
        info=info,
        body_text=body_text,
        sections=sections,
    )


def dry_run_article(url: str, config: "Config") -> None:
    """Print article info without full extraction."""
    try:
        html = fetch_html(url, config.articles, config.network)

        from .deps import ensure_trafilatura
        ensure_trafilatura()
        import trafilatura

        meta = trafilatura.extract_metadata(html, default_url=url)
        title = (meta.title if meta else None) or "Unknown"
        author = (meta.author if meta else None) or "Unknown"
        site = (meta.sitename if meta else None) or "Unknown"
        date = (meta.date if meta else None) or "Unknown"
        lang = (getattr(meta, "language", None) if meta else None) or "Unknown"

        print(f"  Title:     {title}")
        print(f"  Author:    {author}")
        print(f"  Site:      {site}")
        print(f"  Date:      {date}")
        print(f"  Language:  {lang}")
        print(f"  URL:       {url}")
        print()
    except PipelineError as e:
        print(f"  ERROR: {e}")
        print()
