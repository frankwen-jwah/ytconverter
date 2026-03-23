"""Whisper audio transcription fallback — download audio and transcribe."""

import pathlib
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

from .exceptions import WhisperError, YTTranscriptError
from .models import SubtitleCue
from .ytdlp import run_ytdlp


def _pip_install(package: str) -> None:
    """Install a pip package, with fallbacks for --user and --break-system-packages."""
    base = [sys.executable, "-m", "pip", "install", "--quiet"]
    for extra_args in [[], ["--user"], ["--break-system-packages"], ["--user", "--break-system-packages"]]:
        try:
            subprocess.check_call(base + extra_args + [package], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except subprocess.CalledProcessError:
            continue
    raise subprocess.CalledProcessError(1, "pip install")


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
    try:
        _pip_install("imageio-ffmpeg")
    except subprocess.CalledProcessError:
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
        import shutil as _shutil
        _shutil.copy2(ffmpeg_bin, link)


def ensure_faster_whisper() -> None:
    """Ensure faster-whisper is installed. Auto-installs via pip if missing."""
    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        pass
    print("  Installing faster-whisper (first time only, this may take a moment)...")
    try:
        _pip_install("faster-whisper")
    except subprocess.CalledProcessError:
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
                   tmpdir: pathlib.Path, retries: int = 3) -> pathlib.Path:
    """Download audio track via yt-dlp. Returns path to audio file."""
    ensure_ffmpeg()
    args = [
        "-x", "--audio-format", "wav",
        "--audio-quality", "0",
        "--no-warnings",
        "-o", str(tmpdir / "audio.%(ext)s"),
        url,
    ]
    try:
        run_ytdlp(args, cookie_args, retries)
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
                     model_name: str = "base",
                     device_override: Optional[str] = None) -> Tuple[List[SubtitleCue], str]:
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

    print(f"  Loading Whisper model '{model_name}' on {device}...")
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:
        if device == "cuda":
            print(f"  GPU failed ({e}). Falling back to CPU...")
            device, compute_type = "cpu", "int8"
            try:
                model = WhisperModel(model_name, device=device, compute_type=compute_type)
            except Exception as e2:
                raise WhisperError(f"Failed to load Whisper model '{model_name}': {e2}") from e2
        else:
            raise WhisperError(f"Failed to load Whisper model '{model_name}': {e}") from e

    whisper_lang = _normalize_lang_code(lang_hint)
    print(f"  Transcribing audio{f' (language: {whisper_lang})' if whisper_lang else ''}...")

    try:
        segments, info = model.transcribe(
            str(audio_path),
            language=whisper_lang,
            beam_size=5,
            vad_filter=True,
        )
    except Exception as e:
        raise WhisperError(f"Transcription failed: {e}") from e

    cues = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            cues.append(SubtitleCue(segment.start, segment.end, text))

    if not cues:
        raise WhisperError("Whisper produced no transcript segments")

    detected_lang = info.language or whisper_lang or "und"
    print(f"  Transcribed {len(cues)} segments (detected language: {detected_lang})")
    return cues, detected_lang


def whisper_fallback(url: str, cookie_args: List[str], tmpdir: pathlib.Path,
                     lang: Optional[str], model: str,
                     retries: int = 3,
                     device: Optional[str] = None) -> Tuple[List[SubtitleCue], str]:
    """Full Whisper fallback: download audio and transcribe.

    Returns (cues, language_code).
    """
    audio_path = download_audio(url, cookie_args, tmpdir, retries)
    return transcribe_audio(audio_path, lang, model, device)
