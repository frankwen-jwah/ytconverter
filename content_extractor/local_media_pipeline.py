"""Pipeline orchestration — transcribe local video/audio files via Whisper.

Wraps the existing Whisper pipeline so that local media files (.mp4, .mov,
.mkv, .webm, .mp3, .wav, .m4a, ...) are transcribed and returned as a
:class:`TranscriptResult`, reusing the YouTube transcript markdown +
auto-polish flow downstream.
"""

import pathlib
from datetime import datetime
from typing import TYPE_CHECKING

from .exceptions import PipelineError, WhisperError
from .models import TranscriptResult, VideoInfo
from .subtitles import clean_cues
from .whisper import transcribe_audio

if TYPE_CHECKING:
    from .config import Config


def _humanize_stem(stem: str) -> str:
    """Convert a file stem like ``slam_dunk`` into a displayable title."""
    cleaned = stem.replace("_", " ").replace("-", " ").strip()
    return cleaned.title() if cleaned else stem


def _format_duration(total_seconds: int) -> str:
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _build_local_video_info(path: pathlib.Path,
                            duration_seconds: int,
                            language: str) -> VideoInfo:
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return VideoInfo(
        video_id=path.stem,
        title=_humanize_stem(path.stem),
        url=path.resolve().as_uri(),
        channel="(local file)",
        upload_date=mtime.strftime("%Y-%m-%d"),
        duration_seconds=duration_seconds,
        duration_string=_format_duration(duration_seconds),
        language=language,
        chapters=[],
        description="",
    )


def process_single_local_media(file_path: str,
                               config: "Config") -> TranscriptResult:
    """Transcribe a local video/audio file with Whisper.

    Returns a :class:`TranscriptResult` so the YouTube markdown builder and
    auto-polish flow can be reused unchanged.
    """
    if not config.whisper.enabled:
        raise PipelineError(
            "Local media transcription requires Whisper. "
            "Enable it in config.yaml (whisper.enabled: true) or remove --no-whisper."
        )

    path = pathlib.Path(file_path).resolve()
    if not path.exists() or not path.is_file():
        raise WhisperError(f"Local media file not found: {path}")

    print(f"{path.name}", flush=True)
    print(f"  [local-media] Transcribing with Whisper "
          f"({config.whisper.model})...", flush=True)

    device_override = (config.whisper.device
                       if config.whisper.device and config.whisper.device != "auto"
                       else None)
    cues, detected_lang = transcribe_audio(
        path,
        lang_hint=config.subtitles.lang,
        model_name=config.whisper.model,
        device_override=device_override,
        beam_size=config.whisper.beam_size,
        vad_filter=config.whisper.vad_filter,
    )

    cues = clean_cues(cues)
    duration_seconds = int(max((c.end_seconds for c in cues), default=0.0))
    info = _build_local_video_info(path, duration_seconds, detected_lang)
    print(f"  [local-media] Transcribed: {len(cues)} cues, "
          f"language={detected_lang}, duration={info.duration_string}",
          flush=True)

    return TranscriptResult(
        info=info,
        cues=cues,
        sub_language=detected_lang,
        is_auto_generated=False,
        is_whisper_transcribed=True,
    )


def dry_run_local_media(file_path: str, config: "Config") -> None:
    """Print local media file info without transcribing."""
    try:
        p = pathlib.Path(file_path)
        print(f"  File:     {p.name}")
        print(f"  Path:     {p.resolve()}")
        print(f"  Size:     {p.stat().st_size:,} bytes")
        print(f"  Format:   {p.suffix.lower()}")
        print(f"  Pipeline: Whisper transcription "
              f"({config.whisper.model}) + LLM polish")
        print()
    except OSError as e:
        print(f"  ERROR: {e}")
        print()
