"""Pipeline orchestration — single PDF processing and dry-run."""

import pathlib
from typing import Optional, TYPE_CHECKING

from .article import sections_to_body_text
from .exceptions import PipelineError
from .models import ArticleSection, PDFInfo, PDFResult

if TYPE_CHECKING:
    from .config import Config


def process_single_pdf(url: str, config: "Config",
                       local_path: Optional[str] = None) -> PDFResult:
    """Full extraction pipeline for one PDF URL or local file."""
    from .url_detect import is_arxiv_url

    # 1. Resolve arXiv metadata (if applicable)
    arxiv_meta = None
    pdf_url = url
    abs_url = url

    if is_arxiv_url(url):
        from .arxiv import extract_arxiv_id, normalize_arxiv_url, fetch_arxiv_metadata
        arxiv_id = extract_arxiv_id(url)
        if arxiv_id:
            abs_url, pdf_url = normalize_arxiv_url(url)
            print(f"  [pdf] Fetching arXiv metadata for {arxiv_id}...", flush=True)
            arxiv_meta = fetch_arxiv_metadata(arxiv_id, config.network)

    # 2. Get PDF bytes
    if local_path:
        pdf_bytes = pathlib.Path(local_path).read_bytes()
    else:
        from .http_fetch import fetch_pdf_bytes
        print(f"  [pdf] Downloading PDF...", flush=True)
        pdf_bytes = fetch_pdf_bytes(pdf_url, config.pdf, config.network)

    # 3. Extract text and sections via opendataloader-pdf
    # (MarkItDown's pdfminer backend is text-only and drops embedded images;
    # opendataloader-pdf extracts images so the vision pipeline can describe them.)
    from .pdf import extract_pdf_sections
    print(f"  [pdf] Extracting content ({len(pdf_bytes)} bytes)...", flush=True)
    sections, pdf_doc_meta, has_math, images = extract_pdf_sections(
        pdf_bytes, config.pdf, extract_images=config.vision.enabled)

    # 3b. Describe images via vision
    if config.vision.enabled and images:
        from .vision import describe_images, replace_image_markers
        print(f"  [pdf] Describing {len(images)} image(s)...", flush=True)
        descriptions = describe_images(images, config)
        for s in sections:
            s.body = replace_image_markers(s.body, descriptions)

    # 4. Extract abstract from sections
    from .pdf import extract_abstract
    abstract_from_sections, sections = extract_abstract(sections)
    if abstract_from_sections:
        print(f"  [pdf] Abstract extracted ({len(abstract_from_sections)} chars)", flush=True)

    # 5. Build PDFInfo
    if arxiv_meta:
        from .arxiv import build_pdf_info_from_arxiv
        info = build_pdf_info_from_arxiv(
            arxiv_meta, sections,
            pdf_doc_meta["page_count"],
            pdf_doc_meta["word_count"],
        )
        # Use abstract from arXiv API if available, else from PDF body
        if not info.abstract and abstract_from_sections:
            info.abstract = abstract_from_sections
    else:
        info = _build_pdf_info_from_doc(
            pdf_doc_meta, url, pdf_url, sections,
            abstract_from_sections,
        )

    print(f"{info.title}", flush=True)

    # 6. Assemble body text
    body_text = sections_to_body_text(sections)

    return PDFResult(
        info=info,
        body_text=body_text,
        sections=sections,
        has_math=has_math,
    )


def dry_run_pdf(url: str, config: "Config") -> None:
    """Print PDF info without full extraction."""
    from .url_detect import is_arxiv_url

    try:
        if is_arxiv_url(url):
            from .arxiv import extract_arxiv_id, normalize_arxiv_url, fetch_arxiv_metadata
            arxiv_id = extract_arxiv_id(url)
            if arxiv_id:
                abs_url, pdf_url = normalize_arxiv_url(url)
                meta = fetch_arxiv_metadata(arxiv_id, config.network)
                print(f"  Title:      {meta['title']}")
                authors_str = ", ".join(meta["authors"][:5])
                if len(meta["authors"]) > 5:
                    authors_str += f" et al. ({len(meta['authors'])} total)"
                print(f"  Authors:    {authors_str}")
                print(f"  Date:       {meta['publish_date']}")
                print(f"  arXiv ID:   {arxiv_id}")
                if meta["categories"]:
                    print(f"  Categories: {', '.join(meta['categories'])}")
                if meta["doi"]:
                    print(f"  DOI:        {meta['doi']}")
                print(f"  PDF URL:    {pdf_url}")
                print()
                return

        # Non-arXiv: just show the URL (no download in dry-run)
        print(f"  URL:        {url}")
        print(f"  Type:       PDF document")
        print()
    except PipelineError as e:
        print(f"  ERROR: {e}")
        print()


def _build_pdf_info_from_doc(
    pdf_meta: dict,
    url: str,
    pdf_url: str,
    sections: list,
    abstract: str,
) -> PDFInfo:
    """Build PDFInfo from PDF document properties (non-arXiv)."""
    title = pdf_meta.get("title", "").strip()
    if not title:
        # Use first heading as title fallback
        for s in sections:
            if s.heading:
                title = s.heading
                break
        else:
            title = "Untitled PDF"

    author_raw = pdf_meta.get("author", "").strip()
    authors = [a.strip() for a in author_raw.split(",") if a.strip()] if author_raw else []

    return PDFInfo(
        title=title,
        url=url,
        pdf_url=pdf_url,
        authors=authors,
        publish_date=pdf_meta.get("creation_date", "unknown"),
        language=None,
        abstract=abstract,
        categories=[],
        arxiv_id=None,
        doi=None,
        page_count=pdf_meta["page_count"],
        word_count=pdf_meta["word_count"],
        sections=sections,
    )
