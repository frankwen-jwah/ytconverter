"""CLI entry point — argument parsing and batch orchestration."""

import argparse
import pathlib
import sys

# Ensure UTF-8 stdout on Windows (CJK filenames / transcript text)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .config import OUTPUT_DIR, apply_config_defaults, build_cookie_args
from .deps import ensure_yt_dlp
from .exceptions import YTTranscriptError
from .markdown import build_markdown
from .output import make_output_folder, save_transcript
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
    p.add_argument("--cookies", metavar="FILE", type=pathlib.Path,
                   help="Path to Netscape-format cookies.txt file")

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

    # Reprocess existing transcripts
    p.add_argument("--reprocess", metavar="FOLDER", type=pathlib.Path, nargs="+",
                   help="Re-run polish/summarize on existing output folder(s) "
                        "containing transcript.unpolished.md or transcript.md")

    # Behavior
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be extracted without downloading")
    p.add_argument("--retries", type=int, default=3,
                   help="Number of retry attempts for network errors (default: 3)")
    p.add_argument("--polish", action="store_true",
                   help="Polish transcript via Claude CLI (fix punctuation, speech-recognition errors)")
    p.add_argument("--summarize", action="store_true",
                   help="Generate Pyramid/SCQA summary via Claude CLI")
    p.add_argument("--no-whisper", action="store_true",
                   help="Disable Whisper audio transcription fallback when no subtitles are available")
    p.add_argument("--whisper-model", metavar="MODEL", default="base",
                   help="Whisper model size: tiny, base, small, medium, large-v3 (default: base)")
    p.add_argument("--whisper-device", metavar="DEVICE", default="auto",
                   help="Whisper device: auto, cuda, cpu (default: auto)")
    p.add_argument("--model", metavar="MODEL", default=None,
                   help="Claude model alias (opus, sonnet, haiku) or auto-detect best available")
    p.add_argument("--polish-model", metavar="MODEL", default="sonnet",
                   help="Model for polishing (default: sonnet — cheaper/faster since polish is less critical)")

    return p


def _reprocess_folders(folders, args):
    """Re-run polish/summarize on existing output folders."""
    from .llm import set_model, validate_llm_setup

    # Init with the summarize model (--model, default opus);
    # polish will temporarily switch to --polish-model (default sonnet)
    validate_llm_setup(model_override=args.model)

    success, failed = 0, 0
    for i, folder in enumerate(folders, 1):
        folder = folder.resolve()
        print(f"[{i}/{len(folders)}] {folder.name}")
        try:
            unpolished = folder / "transcript.unpolished.md"
            polished = folder / "transcript.md"

            if args.polish:
                if unpolished.exists():
                    source = unpolished
                elif polished.exists():
                    # No unpolished file — polish from transcript.md
                    source = polished
                else:
                    print(f"  SKIP: no transcript found in {folder}")
                    failed += 1
                    continue

                from .llm import polish_transcript
                set_model(args.polish_model)
                polish_transcript(source, polished)
                print(f"  Polished: {folder.name}/transcript.md")
                transcript_path = polished
            else:
                # Summarize only — use best available transcript
                transcript_path = polished if polished.exists() else unpolished
                if not transcript_path.exists():
                    print(f"  SKIP: no transcript found in {folder}")
                    failed += 1
                    continue

            if args.summarize:
                from .llm import summarize_transcript
                set_model(args.model or "opus")
                summary_path = folder / "summary.md"
                summarize_transcript(transcript_path, summary_path)
                print(f"  Summary: {folder.name}/summary.md")

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


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Apply config file defaults (CLI flags override)
    apply_config_defaults(args)

    # Reprocess mode — skip URL handling entirely
    if args.reprocess:
        if not args.polish and not args.summarize:
            parser.error("--reprocess requires --polish and/or --summarize")
        _reprocess_folders(args.reprocess, args)
        return

    # Collect URLs
    urls = list(args.urls or [])
    if args.file:
        if not args.file.exists():
            parser.error(f"URL file not found: {args.file}")
        urls.extend(args.file.read_text().strip().split("\n"))

    if not urls:
        parser.error("No URLs provided. Pass URLs as arguments or use --file.")

    # Validate LLM setup early if polish/summarize requested
    if args.polish or args.summarize:
        from .llm import validate_llm_setup
        validate_llm_setup(model_override=args.model)

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

            # Always create timestamped folder
            folder = make_output_folder(result.info, args.output_dir)

            if args.polish:
                unpolished_path = folder / "transcript.unpolished.md"
                save_transcript(markdown, unpolished_path, args.overwrite)
                print(f"  Saved unpolished: {folder.name}/transcript.unpolished.md")

                from .llm import polish_transcript, set_model
                set_model(args.polish_model)
                polished_path = folder / "transcript.md"
                polish_transcript(unpolished_path, polished_path)
                print(f"  Polished: {folder.name}/transcript.md")
                transcript_path = polished_path
            else:
                transcript_path = folder / "transcript.md"
                save_transcript(markdown, transcript_path, args.overwrite)
                print(f"  Saved: {folder.name}/")

            if args.summarize:
                from .llm import summarize_transcript, set_model
                set_model(args.model or "opus")
                summary_path = folder / "summary.md"
                summarize_transcript(transcript_path, summary_path)
                print(f"  Summary: {folder.name}/summary.md")

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
