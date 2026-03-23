"""CLI entry point — argument parsing and batch orchestration."""

import argparse
import pathlib

from .config import OUTPUT_DIR, apply_config_defaults, build_cookie_args
from .deps import ensure_yt_dlp
from .exceptions import YTTranscriptError
from .markdown import build_markdown
from .output import make_output_path, save_transcript
from .pipeline import dry_run_video, process_single_video
from .ytdlp import resolve_urls


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt_transcript",
        description="Extract YouTube transcripts to Markdown.",
    )
    # Input
    p.add_argument("urls", nargs="*", help="YouTube URL(s) — video, playlist, or channel")
    p.add_argument("-f", "--file", type=pathlib.Path,
                   help="Text file with one URL per line")

    # Auth
    p.add_argument("--cookies-from-browser", metavar="BROWSER",
                   help="Auto-extract cookies from browser (chrome, firefox, edge, safari, opera, brave)")

    # Language
    p.add_argument("--lang", metavar="CODE",
                   help="Force subtitle language code (e.g. en, zh-Hans, ja)")
    p.add_argument("--prefer-auto", action="store_true",
                   help="Prefer auto-generated subs over manual (default: prefer manual)")

    # Output
    p.add_argument("-o", "--output-dir", type=pathlib.Path, default=OUTPUT_DIR,
                   help=f"Output directory (default: {OUTPUT_DIR})")
    p.add_argument("--no-chapters", action="store_true",
                   help="Ignore chapter markers, output flat transcript")
    p.add_argument("--include-description", action="store_true",
                   help="Include video description in output")
    p.add_argument("--overwrite", action="store_true",
                   help="Overwrite existing files")

    # Behavior
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be extracted without downloading")
    p.add_argument("--retries", type=int, default=3,
                   help="Number of retry attempts for network errors (default: 3)")
    p.add_argument("--polish", action="store_true",
                   help="Mark transcript for Claude-based cleanup (use via /yt-transcript command)")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Apply config file defaults (CLI flags override)
    apply_config_defaults(args)

    # Collect URLs
    urls = list(args.urls or [])
    if args.file:
        if not args.file.exists():
            parser.error(f"URL file not found: {args.file}")
        urls.extend(args.file.read_text().strip().split("\n"))

    if not urls:
        parser.error("No URLs provided. Pass URLs as arguments or use --file.")

    # Ensure yt-dlp
    ensure_yt_dlp()

    # Cookie args
    cookie_args = build_cookie_args(args)

    # Resolve playlist/channel URLs
    print("Resolving URLs...")
    video_urls = resolve_urls(urls, cookie_args)
    if not video_urls:
        print("No video URLs found.")
        return

    print(f"Found {len(video_urls)} video(s).\n")

    # Dry run
    if args.dry_run:
        for i, url in enumerate(video_urls, 1):
            print(f"[{i}/{len(video_urls)}]")
            dry_run_video(url, cookie_args, args.retries)
        return

    # Process
    args.output_dir.mkdir(parents=True, exist_ok=True)
    success, failed = 0, 0

    for i, url in enumerate(video_urls, 1):
        print(f"[{i}/{len(video_urls)}] ", end="", flush=True)
        try:
            result = process_single_video(url, cookie_args, args)
            if result.error:
                print(f"  ERROR: {result.error}")
                failed += 1
                continue

            use_chapters = not args.no_chapters
            markdown = build_markdown(result, args.include_description, use_chapters)

            # If --polish, write with .unpolished.md suffix
            if args.polish:
                path = make_output_path(result.info, args.output_dir, suffix=".unpolished.md")
                save_transcript(markdown, path, args.overwrite)
                print(f"  Saved (needs polish): {path.name}")
                print("  Note: Run via /yt-transcript command for Claude-based polishing.")
            else:
                path = make_output_path(result.info, args.output_dir)
                save_transcript(markdown, path, args.overwrite)
                print(f"  Saved: {path.name}")

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
    if success > 0:
        print(f"Output: {args.output_dir}/")
