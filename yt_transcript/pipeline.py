"""Pipeline orchestration — single video processing and dry-run."""

import argparse
import pathlib
import tempfile
from typing import List

from .exceptions import NoSubtitlesError, YTTranscriptError
from .metadata import extract_video_info
from .models import TranscriptResult
from .subtitles import (
    clean_cues,
    deduplicate_auto_subs,
    download_subtitles,
    parse_subtitle_file,
)
from .ytdlp import fetch_video_metadata


def process_single_video(url: str, cookie_args: List[str],
                         args: argparse.Namespace) -> TranscriptResult:
    """Full extraction pipeline for one video URL."""
    # 1. Fetch metadata
    meta = fetch_video_metadata(url, cookie_args, args.retries)
    info = extract_video_info(meta)
    print(f"{info.title}")

    # 2. Download subtitles (or fall back to Whisper)
    is_whisper = False
    with tempfile.TemporaryDirectory(prefix="yt_sub_") as tmpdir:
        tmppath = pathlib.Path(tmpdir)
        try:
            sub_file, lang_code, is_auto = download_subtitles(
                meta, cookie_args, args.lang, args.prefer_auto, tmppath, args.retries
            )
            # 3. Parse subtitles
            cues = parse_subtitle_file(sub_file)
        except NoSubtitlesError:
            if args.no_whisper:
                raise
            print("  No subtitles found — falling back to Whisper audio transcription...")
            from .whisper import whisper_fallback
            cues, lang_code = whisper_fallback(
                url, cookie_args, tmppath, args.lang, args.whisper_model, args.retries
            )
            is_auto = False
            is_whisper = True

    # 4. Clean and deduplicate
    cues = clean_cues(cues)
    if is_auto:
        cues = deduplicate_auto_subs(cues)

    return TranscriptResult(
        info=info,
        cues=cues,
        sub_language=lang_code,
        is_auto_generated=is_auto,
        is_whisper_transcribed=is_whisper,
    )


def dry_run_video(url: str, cookie_args: List[str], retries: int) -> None:
    """Print video info and available subtitles without downloading."""
    try:
        meta = fetch_video_metadata(url, cookie_args, retries)
        info = extract_video_info(meta)
        manual = list((meta.get("subtitles") or {}).keys())
        auto = list((meta.get("automatic_captions") or {}).keys())
        # Filter live_chat
        manual = [k for k in manual if k != "live_chat"]

        print(f"  Title:    {info.title}")
        print(f"  Channel:  {info.channel}")
        print(f"  Date:     {info.upload_date}")
        print(f"  Duration: {info.duration_string}")
        print(f"  Language: {info.language or 'unknown'}")
        print(f"  Chapters: {len(info.chapters)}")
        print(f"  Manual subs:  {manual or 'none'}")
        print(f"  Auto subs:    {auto[:10] or 'none'}" + (" ..." if len(auto) > 10 else ""))
        print()
    except YTTranscriptError as e:
        print(f"  ERROR: {e}")
        print()
