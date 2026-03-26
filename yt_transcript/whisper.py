"""Whisper audio transcription fallback — download audio and transcribe."""

import pathlib
import shutil
import sys
from typing import List, Optional, Tuple

from .deps import _pip_install
from .exceptions import WhisperError, YTTranscriptError
from .models import SubtitleCue
from .ytdlp import run_ytdlp

# Hold references to ALL WhisperModel/segment/info objects so Python never
# garbage-collects them during the pipeline.  ctranslate2's C++ destructor
# segfaults when freeing CUDA resources; deferring to process exit avoids
# the crash.  We use a list because batch runs may create multiple models.
_kept_refs: list = []


def ensure_ffmpeg() -> None:
    """Ensure ffmpeg is available. Auto-installs via imageio-ffmpeg if missing."""
    if shutil.which("ffmpeg"):
        return
    # Check if imageio_ffmpeg is already installed but not symlinked
    try:
        import imageio_ffmpeg
        _symlink_ffmpeg(imageio_ffmpeg.get_ffmpeg_exe())
        return
    except ImportError:
        pass
    print("  ffmpeg not found. Installing imageio-ffmpeg...")
    if not _pip_install("imageio-ffmpeg"):
        raise WhisperError(
            "Failed to install ffmpeg. Install manually: apt install ffmpeg / brew install ffmpeg"
        )
    # Symlink the bundled binary so yt-dlp can find it
    try:
        import imageio_ffmpeg
        _symlink_ffmpeg(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        raise WhisperError(
            "imageio-ffmpeg installed but could not link ffmpeg binary. "
            "Install ffmpeg manually: apt install ffmpeg / brew install ffmpeg"
        )


def _symlink_ffmpeg(ffmpeg_bin: str) -> None:
    """Symlink/copy ffmpeg binary into the active Python environment's bin dir."""
    # Prefer the venv/uv Scripts/bin dir; fall back to ~/.local/bin
    if sys.prefix != sys.base_prefix:
        # We're in a virtual environment (venv, uv, conda, etc.)
        bin_dir = pathlib.Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")
    else:
        bin_dir = pathlib.Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    link = bin_dir / name
    if link.exists():
        return
    # Symlinks may fail on Windows without admin; copy as fallback
    try:
        link.symlink_to(ffmpeg_bin)
    except OSError:
        shutil.copy2(ffmpeg_bin, link)


def ensure_faster_whisper() -> None:
    """Ensure faster-whisper is installed. Auto-installs via pip if missing."""
    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        pass
    print("  Installing faster-whisper (first time only, this may take a moment)...")
    if not _pip_install("faster-whisper"):
        raise WhisperError(
            "Failed to install faster-whisper. Install manually: pip install faster-whisper"
        )


def _normalize_lang_code(lang: Optional[str]) -> Optional[str]:
    """Convert BCP-47 language code to ISO 639-1 for Whisper.

    Examples: zh-Hans -> zh, en-US -> en, ja -> ja, None -> None
    """
    if not lang:
        return None
    base = lang.split("-")[0].lower()
    return base if len(base) <= 3 else None


def download_audio(url: str, cookie_args: List[str],
                   tmpdir: pathlib.Path, retries: int = 3,
                   audio_quality: str = "0",
                   backoff_base: int = 2) -> pathlib.Path:
    """Download audio track via yt-dlp. Returns path to audio file."""
    ensure_ffmpeg()
    args = [
        "-x", "--audio-format", "wav",
        "--audio-quality", audio_quality,
        "--no-warnings",
        "-o", str(tmpdir / "audio.%(ext)s"),
        url,
    ]
    try:
        run_ytdlp(args, cookie_args, retries, backoff_base=backoff_base)
    except YTTranscriptError as e:
        raise WhisperError(f"Audio download failed: {e}") from e

    audio_files = list(tmpdir.glob("audio.*"))
    if not audio_files:
        raise WhisperError("Audio download succeeded but no file found")
    return audio_files[0]


def _detect_device() -> Tuple[str, str]:
    """Detect best device for Whisper/ctranslate2. Returns (device, compute_type)."""
    # faster-whisper uses ctranslate2 — check if CUDA is available
    try:
        import ctranslate2
        cuda_types = ctranslate2.get_supported_compute_types("cuda")
        # If this doesn't raise, CUDA is available; pick best compute type
        if "float16" in cuda_types:
            return "cuda", "float16"
        if "int8_float16" in cuda_types:
            return "cuda", "int8_float16"
        if cuda_types:
            return "cuda", "default"
    except Exception:
        pass
    # Fallback: check torch
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def transcribe_audio(audio_path: pathlib.Path, lang_hint: Optional[str],
                     model_name: str = "large-v3",
                     device_override: Optional[str] = None,
                     beam_size: int = 5,
                     vad_filter: bool = True) -> Tuple[List[SubtitleCue], str]:
    """Transcribe audio file using faster-whisper.

    Returns (cues, detected_language_code).
    device_override: "cuda", "cpu", or None (auto-detect).
    """
    ensure_faster_whisper()
    from faster_whisper import WhisperModel

    if device_override and device_override != "auto":
        device = device_override
        compute_type = "float16" if device == "cuda" else "int8"
    else:
        device, compute_type = _detect_device()

    print(f"  Loading Whisper model '{model_name}' on {device}...", flush=True)
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:
        if device == "cuda":
            print(f"  GPU failed ({e}). Falling back to CPU...", flush=True)
            device, compute_type = "cpu", "int8"
            try:
                model = WhisperModel(model_name, device=device, compute_type=compute_type)
            except Exception as e2:
                raise WhisperError(f"Failed to load Whisper model '{model_name}': {e2}") from e2
        else:
            raise WhisperError(f"Failed to load Whisper model '{model_name}': {e}") from e

    # Immediately stash model so it's never garbage-collected.
    # ctranslate2's C++ destructor segfaults when freeing CUDA resources.
    _kept_refs.append(model)
    print("  [whisper] Model pinned (CUDA destructor workaround).", flush=True)

    whisper_lang = _normalize_lang_code(lang_hint)
    print(f"  Transcribing audio{f' (language: {whisper_lang})' if whisper_lang else ''}...",
          flush=True)

    try:
        segments, info = model.transcribe(
            str(audio_path),
            language=whisper_lang,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
        # Force evaluation of the generator to surface CUDA errors early
        segments = list(segments)
    except Exception as e:
        if device == "cuda":
            print(f"  GPU transcription failed ({e}). Falling back to CPU...",
                  flush=True)
            device, compute_type = "cpu", "int8"
            try:
                model = WhisperModel(model_name, device=device, compute_type=compute_type)
                _kept_refs.append(model)
                segments, info = model.transcribe(
                    str(audio_path),
                    language=whisper_lang,
                    beam_size=beam_size,
                    vad_filter=vad_filter,
                )
                segments = list(segments)
            except Exception as e2:
                raise WhisperError(f"Transcription failed: {e2}") from e2
        else:
            raise WhisperError(f"Transcription failed: {e}") from e

    # Pin segments and info too — they may hold ctranslate2 C++ pointers
    _kept_refs.append(segments)
    _kept_refs.append(info)

    print(f"  [whisper] Extracting {len(segments)} segments...", flush=True)
    cues = []
    for i, segment in enumerate(segments):
        text = segment.text.strip()
        if text:
            cues.append(SubtitleCue(segment.start, segment.end, text))
    print(f"  [whisper] Extracted {len(cues)} non-empty cues.", flush=True)

    detected_lang = info.language or whisper_lang or "und"
    print(f"  [whisper] Detected language: {detected_lang}", flush=True)

    if not cues:
        raise WhisperError("Whisper produced no transcript segments")

    print(f"  Transcribed {len(cues)} segments (detected language: {detected_lang})",
          flush=True)
    return cues, detected_lang


def whisper_fallback(url: str, cookie_args: List[str], tmpdir: pathlib.Path,
                     lang: Optional[str], model: str,
                     retries: int = 3,
                     device: Optional[str] = None,
                     beam_size: int = 5,
                     vad_filter: bool = True,
                     audio_quality: str = "0",
                     backoff_base: int = 2) -> Tuple[List[SubtitleCue], str]:
    """Full Whisper fallback: download audio and transcribe.

    Returns (cues, language_code).
    """
    audio_path = download_audio(url, cookie_args, tmpdir, retries,
                                audio_quality=audio_quality,
                                backoff_base=backoff_base)
    print("  [whisper] Audio downloaded, starting transcription...", flush=True)
    result = transcribe_audio(audio_path, lang, model, device,
                              beam_size=beam_size, vad_filter=vad_filter)
    print("  [whisper] whisper_fallback returning to pipeline.", flush=True)
    return result
