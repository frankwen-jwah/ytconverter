"""Subtitle lifecycle — language selection, download, VTT/SRT parsing, cleanup."""

import html
import pathlib
import re
import tempfile
from typing import Dict, List, Optional, Tuple

from .exceptions import NoSubtitlesError
from .models import SubtitleCue
from .ytdlp import run_ytdlp

# ── Regex constants ────────────────────────────────────────────────────────

_TS_PATTERN = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})"
)
_HTML_TAG = re.compile(r"<[^>]+>")
_VTT_WORD_TS = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")

# ── Language selection ─────────────────────────────────────────────────────


def select_subtitle_lang(meta: dict, forced_lang: Optional[str], prefer_auto: bool) -> Tuple[str, bool]:
    """Pick the best subtitle language. Returns (lang_code, is_auto_generated).

    Raises NoSubtitlesError if nothing available.
    """
    manual_subs = meta.get("subtitles") or {}
    auto_subs = meta.get("automatic_captions") or {}
    declared_lang = meta.get("language") or ""

    # Filter out non-subtitle tracks like live_chat
    manual_subs = {k: v for k, v in manual_subs.items() if k != "live_chat"}
    auto_subs = {k: v for k, v in auto_subs.items() if k != "live_chat"}

    if forced_lang:
        # Exact match first, then prefix match
        for subs, is_auto in [(manual_subs, False), (auto_subs, True)]:
            if forced_lang in subs:
                return forced_lang, is_auto
            # Prefix match: "en" matches "en-US"
            for key in subs:
                if key.startswith(forced_lang):
                    return key, is_auto
        raise NoSubtitlesError(
            f"No subtitles found for language '{forced_lang}'. "
            f"Available manual: {list(manual_subs.keys())}, auto: {list(auto_subs.keys())[:10]}"
        )

    # Auto-detect: prefer the video's declared language
    if prefer_auto:
        source_order = [(auto_subs, True), (manual_subs, False)]
    else:
        source_order = [(manual_subs, False), (auto_subs, True)]

    for subs, is_auto in source_order:
        if not subs:
            continue
        # Try declared language
        if declared_lang and declared_lang in subs:
            return declared_lang, is_auto
        # Prefix match for declared language
        if declared_lang:
            for key in subs:
                if key.startswith(declared_lang):
                    return key, is_auto
        # Fall back to first available
        first_key = next(iter(subs))
        return first_key, is_auto

    raise NoSubtitlesError("No subtitles (manual or auto-generated) available for this video.")


# ── Subtitle download ─────────────────────────────────────────────────────


def download_subtitles(meta: dict, cookie_args: List[str],
                       forced_lang: Optional[str], prefer_auto: bool,
                       tmpdir: pathlib.Path, retries: int = 3) -> Tuple[pathlib.Path, str, bool]:
    """Download subtitle file. Returns (file_path, lang_code, is_auto)."""
    url = meta.get("webpage_url", meta.get("original_url", ""))

    lang_code, is_auto = select_subtitle_lang(meta, forced_lang, prefer_auto)

    sub_flag = "--write-auto-subs" if is_auto else "--write-subs"
    args = [
        sub_flag,
        "--sub-format", "vtt",
        "--sub-langs", lang_code,
        "--skip-download",
        "--no-warnings",
        "-o", str(tmpdir / "%(id)s.%(ext)s"),
        url,
    ]
    run_ytdlp(args, cookie_args, retries)

    # Find the downloaded subtitle file
    for ext in ["vtt", "srt", "ass", "lrc"]:
        pattern = list(tmpdir.glob(f"*.{lang_code}.{ext}"))
        if pattern:
            return pattern[0], lang_code, is_auto
    # Broader search
    sub_files = list(tmpdir.glob("*.vtt")) + list(tmpdir.glob("*.srt"))
    if sub_files:
        return sub_files[0], lang_code, is_auto

    raise NoSubtitlesError(f"Subtitle download succeeded but no file found in {tmpdir}")


# ── VTT/SRT parsing ───────────────────────────────────────────────────────


def timestamp_to_seconds(ts: str) -> float:
    """Convert 'HH:MM:SS.mmm' or 'MM:SS.mmm' to float seconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
        return h * 3600 + m * 60 + s
    elif len(parts) == 2:
        m, s = int(parts[0]), float(parts[1])
        return m * 60 + s
    return float(ts)


def parse_vtt(file_path: pathlib.Path) -> List[SubtitleCue]:
    """Parse a WebVTT file into SubtitleCue list."""
    content = file_path.read_text(encoding="utf-8", errors="replace")
    cues = []
    blocks = re.split(r"\n\n+", content)

    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue
        if lines[0].startswith("WEBVTT") or lines[0].startswith("NOTE") or lines[0].startswith("Kind:"):
            continue

        # Find timestamp line
        ts_idx = None
        for i, line in enumerate(lines):
            if _TS_PATTERN.search(line):
                ts_idx = i
                break
        if ts_idx is None:
            continue

        match = _TS_PATTERN.search(lines[ts_idx])
        start_ts = match.group(1).replace(",", ".")
        end_ts = match.group(2).replace(",", ".")
        start = timestamp_to_seconds(start_ts)
        end = timestamp_to_seconds(end_ts)

        text_lines = lines[ts_idx + 1:]
        text = "\n".join(text_lines)
        # Strip VTT word-level timestamps and HTML tags
        text = _VTT_WORD_TS.sub("", text)
        text = _HTML_TAG.sub("", text)
        text = html.unescape(text)
        text = text.strip()
        if text:
            cues.append(SubtitleCue(start, end, text))

    return cues


def parse_srt(file_path: pathlib.Path) -> List[SubtitleCue]:
    """Parse an SRT file into SubtitleCue list."""
    content = file_path.read_text(encoding="utf-8", errors="replace")
    cues = []
    blocks = re.split(r"\n\n+", content)

    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue

        ts_idx = None
        for i, line in enumerate(lines):
            if _TS_PATTERN.search(line):
                ts_idx = i
                break
        if ts_idx is None:
            continue

        match = _TS_PATTERN.search(lines[ts_idx])
        start_ts = match.group(1).replace(",", ".")
        end_ts = match.group(2).replace(",", ".")
        start = timestamp_to_seconds(start_ts)
        end = timestamp_to_seconds(end_ts)

        text_lines = lines[ts_idx + 1:]
        text = "\n".join(text_lines)
        text = _HTML_TAG.sub("", text)
        text = html.unescape(text)
        text = text.strip()
        if text:
            cues.append(SubtitleCue(start, end, text))

    return cues


def parse_subtitle_file(file_path: pathlib.Path) -> List[SubtitleCue]:
    """Dispatch to appropriate parser based on extension."""
    ext = file_path.suffix.lower()
    if ext == ".vtt":
        return parse_vtt(file_path)
    elif ext == ".srt":
        return parse_srt(file_path)
    else:
        # Try VTT first, then SRT
        cues = parse_vtt(file_path)
        if cues:
            return cues
        return parse_srt(file_path)


# ── Subtitle cleanup ──────────────────────────────────────────────────────


def clean_cues(cues: List[SubtitleCue]) -> List[SubtitleCue]:
    """Normalize whitespace and strip remaining artifacts from cues."""
    result = []
    for cue in cues:
        text = cue.text
        # Collapse whitespace within lines
        text = re.sub(r"[ \t]+", " ", text)
        # Strip leading/trailing per line
        text = "\n".join(line.strip() for line in text.split("\n"))
        text = text.strip()
        if text:
            result.append(SubtitleCue(cue.start_seconds, cue.end_seconds, text))
    return result


def deduplicate_auto_subs(cues: List[SubtitleCue]) -> List[SubtitleCue]:
    """Remove rolling-window duplicates from YouTube auto-generated subs.

    Auto-generated VTT typically shows 2 lines per cue where line 1 repeats
    the previous cue's content. We keep only genuinely new text.
    """
    if not cues:
        return []

    result = []
    prev_lines = set()

    for cue in cues:
        lines = [l.strip() for l in cue.text.split("\n") if l.strip()]
        # Keep only lines not seen in previous cue
        new_lines = [l for l in lines if l not in prev_lines]
        prev_lines = set(lines)

        if new_lines:
            new_text = " ".join(new_lines)
            result.append(SubtitleCue(cue.start_seconds, cue.end_seconds, new_text))

    return result
