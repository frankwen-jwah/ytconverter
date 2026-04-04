"""Pipeline orchestration — single video processing and dry-run."""

import pathlib
import shutil
import sys
import tempfile
from typing import List, TYPE_CHECKING

from .exceptions import PipelineError
from .metadata import extract_video_info
from .models import TranscriptResult
from .subtitles import (
    clean_cues,
    deduplicate_auto_subs,
    download_subtitles,
    parse_subtitle_file,
)
from .ytdlp import fetch_video_metadata

if TYPE_CHECKING:
    from .config import Config


def process_single_video(url: str, cookie_args: List[str],
                         config: "Config") -> TranscriptResult:
    """Full extraction pipeline for one video URL."""
    # 1. Fetch metadata
    meta = fetch_video_metadata(url, cookie_args, config.network.retries,
                                    backoff_base=config.network.backoff_base)
    info = extract_video_info(meta)
    print(f"{info.title}", flush=True)

    # 2. Download subtitles (or fall back to Whisper)
    is_whisper = False
    tmpdir = tempfile.mkdtemp(prefix="yt_sub_")
    tmppath = pathlib.Path(tmpdir)
    try:
        try:
            sub_file, lang_code, is_auto = download_subtitles(
                meta, cookie_args, config.subtitles.lang,
                config.subtitles.prefer_auto, tmppath, config.network.retries,
                backoff_base=config.network.backoff_base
            )
            # 3. Parse subtitles
            cues = parse_subtitle_file(sub_file)
        except PipelineError as e:
            if not config.whisper.enabled:
                raise
            print(f"  Subtitle extraction failed ({type(e).__name__}: {e}) "
                  f"— falling back to Whisper...", flush=True)
            from .whisper import whisper_fallback
            cues, lang_code = whisper_fallback(
                url, cookie_args, tmppath,
                config.subtitles.lang,
                config.whisper.model,
                config.network.retries,
                config.whisper.device,
                beam_size=config.whisper.beam_size,
                vad_filter=config.whisper.vad_filter,
                audio_quality=config.whisper.audio_quality,
                backoff_base=config.network.backoff_base,
            )
            is_auto = False
            is_whisper = True
    finally:
        # Clean up temp dir — tolerate PermissionError on Windows
        # (Whisper/ctranslate2 may hold open file handles until GC)
        print("  [pipeline] Cleaning up temp files...", flush=True)
        try:
            shutil.rmtree(tmpdir)
        except Exception as e:
            if sys.platform != "win32":
                raise
            print(f"  [pipeline] Temp cleanup deferred (Windows lock): {e}",
                  flush=True)

    # 4. Clean and deduplicate
    print(f"  [pipeline] Processing {len(cues)} cues (lang={lang_code}, "
          f"auto={is_auto}, whisper={is_whisper})...", flush=True)
    cues = clean_cues(cues)
    print(f"  [pipeline] clean_cues done: {len(cues)} remaining", flush=True)
    if is_auto:
        cues = deduplicate_auto_subs(cues)
        print(f"  [pipeline] dedup done: {len(cues)} remaining", flush=True)

    print("  [pipeline] Building TranscriptResult...", flush=True)
    result = TranscriptResult(
        info=info,
        cues=cues,
        sub_language=lang_code,
        is_auto_generated=is_auto,
        is_whisper_transcribed=is_whisper,
    )
    print("  [pipeline] process_single_video complete.", flush=True)
    return result


def dry_run_video(url: str, cookie_args: List[str], retries: int,
                  backoff_base: int = 2) -> None:
    """Print video info and available subtitles without downloading."""
    try:
        meta = fetch_video_metadata(url, cookie_args, retries,
                                    backoff_base=backoff_base)
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
    except PipelineError as e:
        print(f"  ERROR: {e}")
        print()
