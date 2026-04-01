"""CLI entry point — argument parsing and batch orchestration."""

import argparse
import pathlib
import sys
import time

# Ensure UTF-8 stdout on Windows (CJK filenames / transcript text)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .config import load_config, apply_cli_overrides, build_cookie_args, Config
from .exceptions import YTTranscriptError
from .markdown import (build_markdown, build_article_markdown, build_pdf_markdown,
                       build_tweet_markdown, build_podcast_markdown)
from .output import make_output_folder, save_transcript, copy_summary_to_batch
from .pipeline import dry_run_video, process_single_video
from .url_detect import classify_url


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt_transcript",
        description="Extract YouTube transcripts, web articles, PDF papers, local files, podcasts, and tweets to Markdown.",
    )
    # Input
    p.add_argument("urls", nargs="*",
                   help="URL(s) or local file path(s)")
    p.add_argument("-f", "--file", type=pathlib.Path,
                   help="Text file with one URL per line")

    # Auth (YouTube-specific)
    p.add_argument("--cookies-from-browser", metavar="BROWSER",
                   help="Auto-extract cookies from browser (chrome, firefox, edge, safari, opera, brave)")
    p.add_argument("--cookies", metavar="FILE", type=pathlib.Path,
                   help="Path to Netscape-format cookies.txt file")

    # Language (YouTube-specific)
    p.add_argument("--lang", metavar="CODE",
                   help="Force subtitle language code (e.g. en, zh-Hans, ja)")
    p.add_argument("--prefer-auto", action="store_true",
                   help="Prefer auto-generated subs over manual")

    # Output
    p.add_argument("-o", "--output-dir", type=pathlib.Path, default=None,
                   help="Output directory (default: see config.yaml)")
    p.add_argument("--no-chapters", action="store_true",
                   help="Ignore chapter markers, output flat transcript")
    p.add_argument("--include-description", action="store_true",
                   help="Include video/article description in output")
    p.add_argument("--overwrite", action="store_true",
                   help="Overwrite existing files")

    # Reprocess existing outputs
    p.add_argument("--reprocess", metavar="FOLDER", type=pathlib.Path, nargs="+",
                   help="Re-run polish/summarize on existing output folder(s)")
    p.add_argument("--backfill-batch", action="store_true",
                   help="Copy all existing summary.md files to batch-process/ folder")

    # Behavior
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be extracted without downloading")
    p.add_argument("--retries", type=int, default=None,
                   help="Number of retry attempts for network errors (default: see config.yaml)")
    p.add_argument("--polish", action="store_true",
                   help="Polish transcript via Claude CLI")
    p.add_argument("--summarize", action="store_true",
                   help="Generate Pyramid/SCQA summary via Claude CLI")
    p.add_argument("--no-whisper", action="store_true",
                   help="Disable Whisper audio transcription fallback")
    p.add_argument("--whisper-model", metavar="MODEL", default=None,
                   help="Whisper model size (default: see config.yaml)")
    p.add_argument("--whisper-device", metavar="DEVICE", default=None,
                   help="Whisper device: auto, cuda, cpu (default: see config.yaml)")
    p.add_argument("--model", metavar="MODEL", default=None,
                   help="Claude model alias (opus, sonnet, haiku) for summarize")
    p.add_argument("--polish-model", metavar="MODEL", default=None,
                   help="Claude model for polishing (default: auto-detect second-best)")

    # PDF-specific
    p.add_argument("--no-abstract", action="store_true",
                   help="Exclude abstract from PDF paper output")
    p.add_argument("--strip-references", action="store_true",
                   help="Strip References/Bibliography section from PDF papers")
    p.add_argument("--max-pages", type=int, default=None,
                   help="Maximum pages to extract from PDF (0=unlimited)")

    # Podcast-specific
    p.add_argument("--max-episodes", type=int, default=None,
                   help="Maximum episodes to extract from a podcast feed (0=unlimited)")

    # Twitter-specific
    p.add_argument("--nitter-instance", metavar="HOST", default=None,
                   help="Nitter instance hostname for tweet extraction")

    return p


# ---------------------------------------------------------------------------
# Shared save + LLM postprocess (DRY #3)
# ---------------------------------------------------------------------------

def _save_and_postprocess(markdown: str, folder: pathlib.Path,
                          basename: str, config: Config) -> None:
    """Save markdown then optionally polish and summarize.

    *basename* is ``"transcript"`` for YouTube, ``"article"`` for web articles,
    ``"paper"`` for PDFs, ``"document"`` for local files, ``"tweet"`` for
    Twitter/X posts, ``"podcast"`` for podcast episodes.
    """
    # Always save the raw content first — before any LLM work — so the
    # extraction is never lost to a downstream crash.
    if config.flags.polish:
        raw_path = folder / f"{basename}.unpolished.md"
    else:
        raw_path = folder / f"{basename}.md"
    save_transcript(markdown, raw_path, config.output.overwrite)
    print(f"  [cli] Saved: {folder.name}/{raw_path.name}", flush=True)

    content_path = raw_path

    # -- LLM post-processing (polish, then summarize) --
    if config.flags.polish:
        from .llm import get_models, polish_transcript, set_model
        primary, secondary = get_models()
        polish_model = config.llm.polish_model or secondary or primary
        print(f"  [cli] Polishing with model: {polish_model}...", flush=True)
        set_model(polish_model)
        polished_path = folder / f"{basename}.md"
        polish_transcript(raw_path, polished_path)
        print(f"  [cli] Polished: {folder.name}/{polished_path.name}", flush=True)
        content_path = polished_path

    if config.flags.summarize:
        from .llm import get_models, summarize_transcript, set_model
        primary, _secondary = get_models()
        summarize_model = config.llm.model or primary
        print(f"  [cli] Summarizing with model: {summarize_model}...", flush=True)
        set_model(summarize_model)
        summary_path = folder / "summary.md"
        summarize_transcript(content_path, summary_path)
        print(f"  [cli] Summary: {folder.name}/summary.md", flush=True)
        copy_summary_to_batch(folder)
        print(f"  [cli] Batch copy: batch-process/{folder.name}.md", flush=True)


# ---------------------------------------------------------------------------
# Reprocess (DRY #4 — auto-detect content type)
# ---------------------------------------------------------------------------

def _detect_basename(folder: pathlib.Path) -> str:
    """Detect whether folder contains a transcript, article, paper, document, tweet, or podcast."""
    for basename in ("transcript", "article", "paper", "document", "tweet", "podcast"):
        if ((folder / f"{basename}.unpolished.md").exists()
                or (folder / f"{basename}.md").exists()):
            return basename
    return "transcript"  # default fallback


def _reprocess_folders(folders, config: Config):
    """Re-run polish/summarize on existing output folders."""
    from .llm import get_models, init_llm, set_model

    init_llm(config)

    success, failed = 0, 0
    for i, folder in enumerate(folders, 1):
        folder = folder.resolve()
        print(f"[{i}/{len(folders)}] {folder.name}")
        try:
            basename = _detect_basename(folder)
            unpolished = folder / f"{basename}.unpolished.md"
            polished = folder / f"{basename}.md"

            if config.flags.polish:
                if unpolished.exists():
                    source = unpolished
                elif polished.exists():
                    source = polished
                else:
                    print(f"  SKIP: no content found in {folder}")
                    failed += 1
                    continue

                from .llm import polish_transcript
                primary, secondary = get_models()
                polish_model = config.llm.polish_model or secondary or primary
                set_model(polish_model)
                polish_transcript(source, polished)
                print(f"  Polished: {folder.name}/{basename}.md")
                transcript_path = polished
            else:
                transcript_path = polished if polished.exists() else unpolished
                if not transcript_path.exists():
                    print(f"  SKIP: no content found in {folder}")
                    failed += 1
                    continue

            if config.flags.summarize:
                from .llm import summarize_transcript
                primary, _secondary = get_models()
                set_model(config.llm.model or primary)
                summary_path = folder / "summary.md"
                summarize_transcript(transcript_path, summary_path)
                print(f"  Summary: {folder.name}/summary.md")
                copy_summary_to_batch(folder)
                print(f"  Batch copy: batch-process/{folder.name}.md")

            success += 1
        except YTTranscriptError as e:
            print(f"  ERROR: {e}")
            failed += 1
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            break
        except Exception as e:
            print(f"  UNEXPECTED ERROR: {type(e).__name__}: {e}")
            failed += 1

    print(f"\nDone: {success} succeeded, {failed} failed.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()

    # Load config (YAML > migrate JSON > generate template > defaults)
    # then apply CLI overrides on top
    config = load_config()
    config = apply_cli_overrides(config, args)

    # Reprocess mode — skip URL handling entirely
    if args.reprocess:
        if not config.flags.polish and not config.flags.summarize:
            parser.error("--reprocess requires --polish and/or --summarize")
        _reprocess_folders(args.reprocess, config)
        return

    # Backfill batch-process/ folder from existing outputs
    if args.backfill_batch:
        output_root = pathlib.Path(config.output.dir) / "output"
        if not output_root.exists():
            print(f"Output directory not found: {output_root}")
            return

        skip_dirs = {"archive", "book_notes", "batch-process"}
        copied, skipped = 0, 0

        for entry in sorted(output_root.iterdir()):
            if not entry.is_dir() or entry.name in skip_dirs:
                continue
            if (entry / "summary.md").exists():
                copy_summary_to_batch(entry)
                print(f"  Copied: {entry.name} -> batch-process/{entry.name}.md")
                copied += 1
            else:
                print(f"  Skipped (no summary.md): {entry.name}")
                skipped += 1

        print(f"\nBackfill complete: {copied} copied, {skipped} skipped.")
        return

    # Collect URLs
    urls = list(config.urls or [])
    if args.file:
        if not args.file.exists():
            parser.error(f"URL file not found: {args.file}")
        urls.extend(args.file.read_text().strip().split("\n"))

    if not urls:
        parser.error("No URLs or file paths provided. Pass URLs/paths as arguments or use --file.")

    # Validate LLM setup early if polish/summarize requested
    if config.flags.polish or config.flags.summarize:
        from .llm import init_llm
        init_llm(config)

    # Detect local files before URL classification
    from .url_detect import classify_local_path, strip_path_quotes
    local_files = {}  # url_or_path → (abs_path, content_type)
    resolved_urls = []
    for u in urls:
        # Strip accidental quote wrappers (e.g. r"path" from Python syntax)
        clean = strip_path_quotes(u)
        if clean != u:
            print(f"  Note: stripped quotes from path: {u} → {clean}", flush=True)
            u = clean
        local_type = classify_local_path(u)
        if local_type:
            local_files[u] = (str(pathlib.Path(u).resolve()), local_type)
        resolved_urls.append(u)
    urls = resolved_urls

    # Classify URLs
    yt_urls = [u for u in urls if u not in local_files and classify_url(u) == "youtube"]
    pdf_urls = [u for u in urls if (u in local_files and local_files[u][1] == "pdf")
                or (u not in local_files and classify_url(u) == "pdf")]
    local_file_urls = [u for u in urls if u in local_files
                       and local_files[u][1] == "local_file"]
    twitter_urls = [u for u in urls if u not in local_files
                    and classify_url(u) == "twitter"]
    podcast_urls = [u for u in urls if u not in local_files
                    and classify_url(u) == "podcast"]
    article_urls = [u for u in urls if u not in local_files
                    and classify_url(u) == "article"]

    # Build cookie args for yt-dlp (needed by YouTube and podcast pipelines)
    all_items = []  # list of (url, content_type)
    if yt_urls or podcast_urls:
        cookie_args = build_cookie_args(config)
    else:
        cookie_args = []

    if yt_urls:
        from .deps import ensure_yt_dlp
        ensure_yt_dlp()
        from .ytdlp import resolve_urls
        print("Resolving YouTube URLs...")
        video_urls = resolve_urls(yt_urls, cookie_args)
        all_items.extend((u, "youtube") for u in video_urls)

    # Resolve podcast feed URLs (expand RSS to individual episodes)
    podcast_episode_meta = {}  # audio_url → episode metadata dict
    if podcast_urls:
        from .podcast_pipeline import resolve_podcast_feed
        for feed_url in podcast_urls:
            episodes = resolve_podcast_feed(feed_url, config)
            for audio_url, meta in episodes:
                podcast_episode_meta[audio_url] = meta
                all_items.append((audio_url, "podcast"))

    all_items.extend((u, "pdf") for u in pdf_urls)
    all_items.extend((u, "local_file") for u in local_file_urls)
    all_items.extend((u, "twitter") for u in twitter_urls)
    all_items.extend((u, "article") for u in article_urls)

    if not all_items:
        print("No URLs to process.")
        return

    yt_count = sum(1 for _, t in all_items if t == "youtube")
    pdf_count = sum(1 for _, t in all_items if t == "pdf")
    lf_count = sum(1 for _, t in all_items if t == "local_file")
    tw_count = sum(1 for _, t in all_items if t == "twitter")
    pod_count = sum(1 for _, t in all_items if t == "podcast")
    art_count = sum(1 for _, t in all_items if t == "article")
    parts = []
    if yt_count:
        parts.append(f"{yt_count} video(s)")
    if pdf_count:
        parts.append(f"{pdf_count} paper(s)")
    if lf_count:
        parts.append(f"{lf_count} local file(s)")
    if tw_count:
        parts.append(f"{tw_count} tweet(s)")
    if pod_count:
        parts.append(f"{pod_count} podcast episode(s)")
    if art_count:
        parts.append(f"{art_count} article(s)")
    print(f"Found {', '.join(parts)}.\n")

    # Dry run
    if args.dry_run:
        for i, (url, content_type) in enumerate(all_items, 1):
            print(f"[{i}/{len(all_items)}]")
            if content_type == "youtube":
                dry_run_video(url, cookie_args, config.network.retries,
                              backoff_base=config.network.backoff_base)
            elif content_type == "pdf":
                from .pdf_pipeline import dry_run_pdf
                dry_run_pdf(url, config)
            elif content_type == "local_file":
                from .local_file_pipeline import dry_run_local_file
                dry_run_local_file(local_files[url][0], config)
            elif content_type == "twitter":
                from .tweet_pipeline import dry_run_tweet
                dry_run_tweet(url, config)
            elif content_type == "podcast":
                from .podcast_pipeline import dry_run_podcast
                dry_run_podcast(url, cookie_args, config)
            else:
                from .article_pipeline import dry_run_article
                dry_run_article(url, config)
        return

    # Process
    output_dir = pathlib.Path(config.output.dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    success, failed = 0, 0

    for i, (url, content_type) in enumerate(all_items, 1):
        if i > 1 and len(all_items) > 1 and content_type != "local_file":
            time.sleep(2)  # Brief pause to avoid 429 rate limits
        print(f"[{i}/{len(all_items)}] ", end="", flush=True)
        try:
            if content_type == "youtube":
                result = process_single_video(url, cookie_args, config)
                if result.error:
                    print(f"  ERROR: {result.error}", flush=True)
                    failed += 1
                    continue

                use_chapters = not config.flags.no_chapters
                chap_tag = "with" if use_chapters and result.info.chapters else "no"
                print(f"  [cli] Building markdown ({len(result.cues)} cues, "
                      f"{chap_tag} chapters)...", flush=True)
                markdown = build_markdown(
                    result, config.flags.include_description, use_chapters,
                    text_config=config.text)
                folder = make_output_folder(
                    result.info.title, result.info.upload_date,
                    output_dir,
                    slug_max_length=config.output.slug_max_length)
                basename = "transcript"

            elif content_type == "pdf":
                from .pdf_pipeline import process_single_pdf
                lf = local_files.get(url)
                result = process_single_pdf(
                    url, config,
                    local_path=lf[0] if lf else None,
                )
                if result.error:
                    print(f"  ERROR: {result.error}", flush=True)
                    failed += 1
                    continue

                print(f"  [cli] Building PDF markdown "
                      f"({result.info.word_count} words, "
                      f"{len(result.sections)} sections, "
                      f"{result.info.page_count} pages)...", flush=True)
                markdown = build_pdf_markdown(
                    result, config.pdf.include_abstract)
                folder = make_output_folder(
                    result.info.title, result.info.publish_date,
                    output_dir,
                    slug_max_length=config.output.slug_max_length)
                basename = "paper"

            elif content_type == "local_file":
                from .local_file_pipeline import process_single_local_file
                result = process_single_local_file(
                    local_files[url][0], config)
                if result.error:
                    print(f"  ERROR: {result.error}", flush=True)
                    failed += 1
                    continue

                print(f"  [cli] Building document markdown "
                      f"({result.info.word_count} words, "
                      f"{len(result.sections)} sections)...", flush=True)
                markdown = build_article_markdown(
                    result, config.flags.include_description,
                    content_type="document")
                folder = make_output_folder(
                    result.info.title, result.info.publish_date,
                    output_dir,
                    slug_max_length=config.output.slug_max_length)
                basename = "document"

            elif content_type == "twitter":
                from .tweet_pipeline import process_single_tweet
                result = process_single_tweet(url, config)
                if result.error:
                    print(f"  ERROR: {result.error}", flush=True)
                    failed += 1
                    continue

                print(f"  [cli] Building tweet markdown "
                      f"({result.info.word_count} words, "
                      f"{result.info.thread_length} post(s))...", flush=True)
                markdown = build_tweet_markdown(result)
                folder = make_output_folder(
                    result.info.title, result.info.publish_date,
                    output_dir,
                    slug_max_length=config.output.slug_max_length)
                basename = "tweet"

            elif content_type == "podcast":
                from .podcast_pipeline import process_single_podcast
                result = process_single_podcast(
                    url, cookie_args, config,
                    episode_meta=podcast_episode_meta.get(url))
                if result.error:
                    print(f"  ERROR: {result.error}", flush=True)
                    failed += 1
                    continue

                print(f"  [cli] Building podcast markdown "
                      f"({len(result.cues)} cues)...", flush=True)
                markdown = build_podcast_markdown(
                    result, config.flags.include_description,
                    text_config=config.text)
                folder = make_output_folder(
                    result.info.title, result.info.publish_date,
                    output_dir,
                    slug_max_length=config.output.slug_max_length)
                basename = "podcast"

            else:  # article
                from .article_pipeline import process_single_article
                result = process_single_article(url, config)
                if result.error:
                    print(f"  ERROR: {result.error}", flush=True)
                    failed += 1
                    continue

                print(f"  [cli] Building article markdown "
                      f"({result.info.word_count} words, "
                      f"{len(result.sections)} sections)...", flush=True)
                markdown = build_article_markdown(
                    result, config.flags.include_description)
                folder = make_output_folder(
                    result.info.title, result.info.publish_date,
                    output_dir,
                    slug_max_length=config.output.slug_max_length)
                basename = "article"

            print(f"  [cli] Markdown generated: {len(markdown)} chars", flush=True)
            print(f"  [cli] Output folder: {folder.name}/", flush=True)
            _save_and_postprocess(markdown, folder, basename, config)
            success += 1

        except YTTranscriptError as e:
            print(f"  ERROR: {e}", flush=True)
            failed += 1
        except KeyboardInterrupt:
            print("\nInterrupted by user.", flush=True)
            break
        except Exception as e:
            print(f"  UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\nDone: {success} succeeded, {failed} failed.")
    if success > 0:
        print(f"Output: {output_dir}/")
