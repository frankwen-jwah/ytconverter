#!/usr/bin/env python3
"""YouTube transcript extraction pipeline.

Extracts transcripts from YouTube videos (including member-only content)
and saves them as structured Markdown with chapter sections.
Auto-installs yt-dlp if not present. No paid API costs.
"""

import argparse
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
OUTPUT_DIR = SCRIPT_DIR / "yt_transcripts"
CONFIG_FILE = OUTPUT_DIR / ".config.json"

# ── Exceptions ──────────────────────────────────────────────────────────────

class YTTranscriptError(Exception):
    """Base exception for this pipeline."""

class NoSubtitlesError(YTTranscriptError):
    """Video has no subtitles available."""

class AuthRequiredError(YTTranscriptError):
    """Video requires authentication but no cookies provided."""

class VideoUnavailableError(YTTranscriptError):
    """Video is private, deleted, or region-locked."""

class NetworkError(YTTranscriptError):
    """Network error after all retries exhausted."""

# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str

@dataclass
class Chapter:
    title: str
    start_seconds: float
    end_seconds: float

@dataclass
class VideoInfo:
    video_id: str
    title: str
    url: str
    channel: str
    upload_date: str          # YYYY-MM-DD
    duration_seconds: int
    duration_string: str      # H:MM:SS
    language: Optional[str]
    chapters: List[Chapter]
    description: str

@dataclass
class TranscriptResult:
    info: VideoInfo
    cues: List[SubtitleCue]
    sub_language: str
    is_auto_generated: bool
    error: Optional[str] = None

# ── Dependency management ───────────────────────────────────────────────────

def ensure_yt_dlp() -> str:
    """Ensure yt-dlp is installed. Returns path to binary."""
    path = shutil.which("yt-dlp")
    if path:
        return path
    print("yt-dlp not found. Installing via pip...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "yt-dlp"],
            stdout=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        # Fallback: try with --user
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", "--user", "yt-dlp"],
                stdout=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            print("ERROR: Failed to install yt-dlp. Install manually: pip install yt-dlp", file=sys.stderr)
            sys.exit(1)
    path = shutil.which("yt-dlp")
    if not path:
        # pip may have installed to a path not in PATH; try common locations
        for candidate in [
            pathlib.Path(sys.prefix) / "bin" / "yt-dlp",
            pathlib.Path.home() / ".local" / "bin" / "yt-dlp",
        ]:
            if candidate.exists():
                return str(candidate)
        print("ERROR: yt-dlp installed but not found in PATH.", file=sys.stderr)
        sys.exit(1)
    return path

# ── CLI ─────────────────────────────────────────────────────────────────────

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
    p.add_argument("--save-cookie-pref", action="store_true",
                   help="Remember cookie setting for future runs")

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

# ── Cookie config persistence ──────────────────────────────────────────────

def load_cookie_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def save_cookie_config(cookie_args: List[str]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"cookie_args": cookie_args}, indent=2))

def build_cookie_args(args: argparse.Namespace) -> List[str]:
    if args.cookies_from_browser:
        return ["--cookies-from-browser", args.cookies_from_browser]
    # Check saved config
    cfg = load_cookie_config()
    return cfg.get("cookie_args", [])

# ── yt-dlp interaction ──────────────────────────────────────────────────────

def run_ytdlp(args: List[str], cookie_args: List[str], retries: int = 3) -> subprocess.CompletedProcess:
    """Run yt-dlp with given args. Retries on network errors."""
    cmd = ["yt-dlp"] + cookie_args + args
    last_err = None
    for attempt in range(retries):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result
        stderr = result.stderr.lower()
        # Classify error
        if any(p in stderr for p in ["video unavailable", "private video", "this video has been removed"]):
            raise VideoUnavailableError(result.stderr.strip())
        if any(p in stderr for p in ["sign in", "requires authentication", "members-only", "member", "join this channel"]):
            raise AuthRequiredError(
                "This video requires authentication. Use --cookies-from-browser chrome"
            )
        if any(p in stderr for p in ["unable to download", "http error", "connection", "urlopen error", "timed out"]):
            last_err = result.stderr.strip()
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  Network error, retrying in {wait}s... (attempt {attempt+2}/{retries})")
                time.sleep(wait)
                continue
            raise NetworkError(f"Network error after {retries} attempts: {last_err}")
        # Unknown error
        raise YTTranscriptError(result.stderr.strip() or f"yt-dlp exited with code {result.returncode}")
    raise NetworkError(f"Failed after {retries} attempts: {last_err}")


def fetch_video_metadata(url: str, cookie_args: List[str], retries: int = 3) -> dict:
    """Fetch video metadata as JSON dict."""
    result = run_ytdlp(
        ["--dump-json", "--skip-download", "--no-warnings", url],
        cookie_args, retries
    )
    return json.loads(result.stdout)


def resolve_urls(raw_urls: List[str], cookie_args: List[str]) -> List[str]:
    """Expand playlist/channel URLs to individual video URLs. Deduplicate."""
    video_urls = []
    seen = set()
    for url in raw_urls:
        url = url.strip()
        if not url or url.startswith("#"):
            continue
        # Check if this is a playlist or channel
        if "playlist" in url or "/c/" in url or "/channel/" in url or "/@" in url:
            try:
                result = run_ytdlp(
                    ["--flat-playlist", "--dump-json", "--no-warnings", url],
                    cookie_args, retries=2
                )
                for line in result.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    vid_url = entry.get("url") or entry.get("webpage_url", "")
                    if vid_url and "watch" not in vid_url:
                        vid_url = f"https://www.youtube.com/watch?v={entry.get('id', '')}"
                    if vid_url and vid_url not in seen:
                        seen.add(vid_url)
                        video_urls.append(vid_url)
            except YTTranscriptError as e:
                print(f"  Warning: could not expand {url}: {e}")
                # Try as single video
                if url not in seen:
                    seen.add(url)
                    video_urls.append(url)
        else:
            if url not in seen:
                seen.add(url)
                video_urls.append(url)
    return video_urls

# ── Metadata parsing ───────────────────────────────────────────────────────

def format_duration(seconds: int) -> str:
    """Convert seconds to H:MM:SS or M:SS."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_upload_date(raw: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD."""
    if raw and len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw or "unknown"


def extract_video_info(meta: dict) -> VideoInfo:
    """Parse yt-dlp JSON metadata into VideoInfo."""
    chapters = []
    for ch in (meta.get("chapters") or []):
        chapters.append(Chapter(
            title=ch.get("title", "Untitled"),
            start_seconds=ch.get("start_time", 0),
            end_seconds=ch.get("end_time", 0),
        ))
    dur = int(meta.get("duration") or 0)
    return VideoInfo(
        video_id=meta.get("id", ""),
        title=meta.get("title", "Untitled"),
        url=meta.get("webpage_url", meta.get("original_url", "")),
        channel=meta.get("channel", meta.get("uploader", "Unknown")),
        upload_date=parse_upload_date(meta.get("upload_date", "")),
        duration_seconds=dur,
        duration_string=format_duration(dur),
        language=meta.get("language"),
        chapters=chapters,
        description=meta.get("description", ""),
    )

# ── Language selection & subtitle download ──────────────────────────────────

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


def download_subtitles(meta: dict, cookie_args: List[str],
                       forced_lang: Optional[str], prefer_auto: bool,
                       tmpdir: pathlib.Path, retries: int = 3) -> Tuple[pathlib.Path, str, bool]:
    """Download subtitle file. Returns (file_path, lang_code, is_auto)."""
    video_id = meta.get("id", "video")
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
        # Infer language from filename
        return sub_files[0], lang_code, is_auto

    raise NoSubtitlesError(f"Subtitle download succeeded but no file found in {tmpdir}")

# ── Subtitle parsing ───────────────────────────────────────────────────────

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


_TS_PATTERN = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})"
)
_HTML_TAG = re.compile(r"<[^>]+>")
_VTT_WORD_TS = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")


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

# ── Subtitle cleanup ───────────────────────────────────────────────────────

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

# ── Chapter alignment ──────────────────────────────────────────────────────

def align_cues_to_chapters(cues: List[SubtitleCue],
                           chapters: List[Chapter]) -> Dict[int, List[SubtitleCue]]:
    """Assign each cue to its chapter. Single-pass O(n) merge."""
    if not chapters:
        return {0: cues}

    result: Dict[int, List[SubtitleCue]] = {i: [] for i in range(len(chapters))}
    ch_idx = 0

    for cue in cues:
        while (ch_idx < len(chapters) - 1 and
               cue.start_seconds >= chapters[ch_idx + 1].start_seconds):
            ch_idx += 1
        result[ch_idx].append(cue)

    return result

# ── Text assembly ──────────────────────────────────────────────────────────

_CJK_RANGE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef"
    r"\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]"
)
_SENTENCE_END = re.compile(r"[.!?。！？]\s*$")


def is_cjk_dominant(text: str) -> bool:
    """Check if >30% of non-whitespace chars are CJK."""
    chars = re.sub(r"\s", "", text)
    if not chars:
        return False
    cjk_count = len(_CJK_RANGE.findall(chars))
    return cjk_count / len(chars) > 0.3


def cues_to_text(cues: List[SubtitleCue]) -> str:
    """Convert subtitle cues into readable paragraph text."""
    if not cues:
        return ""

    # Determine if CJK dominant
    sample = " ".join(c.text for c in cues[:50])
    cjk_mode = is_cjk_dominant(sample)
    joiner = "" if cjk_mode else " "

    paragraphs = []
    current_para = []
    sentence_count = 0
    prev_end = cues[0].start_seconds

    for cue in cues:
        # Gap-based paragraph break: >4 seconds silence
        if current_para and (cue.start_seconds - prev_end) > 4.0:
            paragraphs.append(joiner.join(current_para))
            current_para = []
            sentence_count = 0

        current_para.append(cue.text)

        if _SENTENCE_END.search(cue.text):
            sentence_count += 1

        # Sentence-count paragraph break
        if sentence_count >= 6:
            paragraphs.append(joiner.join(current_para))
            current_para = []
            sentence_count = 0

        prev_end = cue.end_seconds

    if current_para:
        paragraphs.append(joiner.join(current_para))

    return "\n\n".join(paragraphs)

# ── Markdown generation ─────────────────────────────────────────────────────

def escape_yaml_string(s: str) -> str:
    """Escape a string for YAML double-quoted value."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_markdown(result: TranscriptResult, include_description: bool,
                   use_chapters: bool) -> str:
    """Assemble the final Markdown document."""
    info = result.info
    lines = []

    # YAML frontmatter
    lines.append("---")
    lines.append(f'title: "{escape_yaml_string(info.title)}"')
    lines.append(f'url: "{info.url}"')
    lines.append(f'channel: "{escape_yaml_string(info.channel)}"')
    lines.append(f'date: "{info.upload_date}"')
    lines.append(f'language: "{result.sub_language}"')
    lines.append(f'duration: "{info.duration_string}"')
    lines.append(f"auto_generated: {str(result.is_auto_generated).lower()}")
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# {info.title}")
    lines.append("")

    # Metadata line
    lines.append(f"> Channel: {info.channel} | Date: {info.upload_date} | Duration: {info.duration_string}")
    lines.append("")

    # Auto-generated warning
    if result.is_auto_generated:
        lines.append("*Auto-generated transcript — may contain errors.*")
        lines.append("")

    # Description (collapsible)
    if include_description and info.description:
        lines.append("<details>")
        lines.append("<summary>Video Description</summary>")
        lines.append("")
        lines.append(info.description)
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Transcript body
    if use_chapters and info.chapters:
        chapter_cues = align_cues_to_chapters(result.cues, info.chapters)
        for i, chapter in enumerate(info.chapters):
            lines.append(f"## {chapter.title}")
            lines.append("")
            text = cues_to_text(chapter_cues.get(i, []))
            if text:
                lines.append(text)
            else:
                lines.append("*(No transcript for this section)*")
            lines.append("")
    else:
        text = cues_to_text(result.cues)
        if text:
            lines.append(text)
        else:
            lines.append("*(No transcript text extracted)*")
        lines.append("")

    return "\n".join(lines)

# ── Output & filename handling ──────────────────────────────────────────────

def slugify(text: str, max_length: int = 80) -> str:
    """Convert text to filesystem-safe slug. Keeps CJK characters."""
    # Normalize unicode
    text = unicodedata.normalize("NFKC", text)
    # Lowercase (only affects Latin chars, CJK unaffected)
    text = text.lower()
    # Replace spaces and common separators with hyphens
    text = re.sub(r"[\s_]+", "-", text)
    # Keep alphanumeric, hyphens, and CJK characters
    text = re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)
    # Collapse multiple hyphens
    text = re.sub(r"-{2,}", "-", text)
    # Strip leading/trailing hyphens
    text = text.strip("-")
    # Truncate
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text or "untitled"


def make_output_path(info: VideoInfo, output_dir: pathlib.Path,
                     suffix: str = ".md") -> pathlib.Path:
    """Generate output path: output_dir/YYYY-MM-DD_slug.md with collision handling."""
    slug = slugify(info.title)
    base_name = f"{info.upload_date}_{slug}"
    path = output_dir / f"{base_name}{suffix}"

    counter = 2
    while path.exists():
        path = output_dir / f"{base_name}-{counter}{suffix}"
        counter += 1

    return path


def save_transcript(markdown: str, path: pathlib.Path, overwrite: bool) -> pathlib.Path:
    """Write markdown to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        # Shouldn't happen due to make_output_path collision handling, but safety check
        print(f"  File exists, skipping: {path.name}")
        return path
    path.write_text(markdown, encoding="utf-8")
    return path

# ── Single video pipeline ──────────────────────────────────────────────────

def process_single_video(url: str, cookie_args: List[str],
                         args: argparse.Namespace) -> TranscriptResult:
    """Full extraction pipeline for one video URL."""
    # 1. Fetch metadata
    meta = fetch_video_metadata(url, cookie_args, args.retries)
    info = extract_video_info(meta)
    print(f"{info.title}")

    # 2. Download subtitles
    prefer_manual = not args.prefer_auto
    with tempfile.TemporaryDirectory(prefix="yt_sub_") as tmpdir:
        tmppath = pathlib.Path(tmpdir)
        sub_file, lang_code, is_auto = download_subtitles(
            meta, cookie_args, args.lang, args.prefer_auto, tmppath, args.retries
        )

        # 3. Parse subtitles
        cues = parse_subtitle_file(sub_file)

    # 4. Clean and deduplicate
    cues = clean_cues(cues)
    if is_auto:
        cues = deduplicate_auto_subs(cues)

    return TranscriptResult(
        info=info,
        cues=cues,
        sub_language=lang_code,
        is_auto_generated=is_auto,
    )

# ── Dry-run ─────────────────────────────────────────────────────────────────

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

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args = parser.parse_args()

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
    if args.save_cookie_pref and cookie_args:
        save_cookie_config(cookie_args)
        print(f"Cookie preference saved: {' '.join(cookie_args)}")

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


if __name__ == "__main__":
    main()
