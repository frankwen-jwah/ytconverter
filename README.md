# YouTube Transcript Extractor

Extract full transcripts from YouTube videos and save them as structured Markdown files with chapter sections, auto language detection, and member-only content support.

## Features

- **Chapter preservation** — Video chapters become `##` headers in the output
- **Auto language detection** — Detects video language; saves transcript in the original language (Chinese, English, Japanese, etc.)
- **Member-only content** — Authenticate via browser cookies for subscriber/membership videos
- **Manual & auto-generated subs** — Prefers manual subtitles; falls back to auto-generated with smart deduplication
- **CJK-aware formatting** — Chinese/Japanese text joined without extra spaces; proper paragraph breaks
- **Batch processing** — Single URLs, multiple URLs, playlists, channels, or URL files
- **Optional Claude polish** — Clean up auto-generated transcript artifacts via `/yt-transcript` command

## Requirements

- Python 3.8+
- yt-dlp (auto-installed on first run)

## Quick Start

```bash
# Extract transcript from a public video
python3 yt_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Preview available subtitles without downloading
python3 yt_transcript.py --dry-run "https://www.youtube.com/watch?v=VIDEO_ID"
```

Output is saved to `./yt_transcripts/YYYY-MM-DD_video-title-slug.md`.

## Usage

```
python3 yt_transcript.py [OPTIONS] [URLs...]
```

### Input Options

| Option | Description |
|--------|-------------|
| `URLs...` | One or more YouTube video, playlist, or channel URLs |
| `-f, --file FILE` | Text file with one URL per line |

### Authentication

| Option | Description |
|--------|-------------|
| `--cookies-from-browser BROWSER` | Extract cookies from browser (chrome, firefox, edge, safari, opera, brave) |
| `--save-cookie-pref` | Remember cookie setting for future runs |

### Language

| Option | Description |
|--------|-------------|
| `--lang CODE` | Force subtitle language (e.g. `en`, `zh-Hans`, `ja`) |
| `--prefer-auto` | Prefer auto-generated subs over manual |

### Output

| Option | Description |
|--------|-------------|
| `-o, --output-dir DIR` | Output directory (default: `./yt_transcripts/`) |
| `--no-chapters` | Ignore chapter markers, output flat transcript |
| `--include-description` | Include video description in output |
| `--overwrite` | Overwrite existing files |

### Behavior

| Option | Description |
|--------|-------------|
| `--dry-run` | Show video info and available subs without downloading |
| `--retries N` | Retry attempts for network errors (default: 3) |
| `--polish` | Mark transcript for Claude-based cleanup |

## Examples

```bash
# Member-only content via Chrome cookies
python3 yt_transcript.py --cookies-from-browser chrome "https://www.youtube.com/watch?v=MEMBER_VIDEO"

# Save cookie preference so you don't repeat it
python3 yt_transcript.py --cookies-from-browser chrome --save-cookie-pref "URL"

# Extract a full playlist
python3 yt_transcript.py "https://www.youtube.com/playlist?list=PLxxxxxxx"

# Batch from a file of URLs
python3 yt_transcript.py -f urls.txt

# Force Chinese subtitles
python3 yt_transcript.py --lang zh-Hans "URL"

# Flat transcript without chapter sections
python3 yt_transcript.py --no-chapters "URL"

# Include video description in output
python3 yt_transcript.py --include-description "URL"
```

## Output Format

```markdown
---
title: "Video Title"
url: "https://youtube.com/watch?v=..."
channel: "Channel Name"
date: "2024-01-15"
language: "en"
duration: "1:23:45"
auto_generated: true
---

# Video Title

> Channel: Channel Name | Date: 2024-01-15 | Duration: 1:23:45

*Auto-generated transcript — may contain errors.*

## Chapter 1 Title

Transcript paragraphs for chapter 1...

## Chapter 2 Title

Transcript paragraphs for chapter 2...
```

If the video has no chapters, the transcript appears as a single body under the title.

## Claude Integration

When used with [Claude Code](https://claude.com/claude-code):

- **`/yt-transcript <URL>`** — Slash command that runs the pipeline and optionally polishes output
- **`--polish` flag** — Saves `.unpolished.md` first, then Claude fixes punctuation, capitalization, and speech-recognition errors while preserving the original language

## How It Works

1. **Metadata fetch** (`ytdlp.py`) — yt-dlp fetches video info, chapters, available subtitle tracks
2. **Language selection** (`subtitles.py`) — Auto-detected from video metadata; manual subs preferred over auto-generated
3. **Subtitle download** (`subtitles.py`) — WebVTT format to a temp directory
4. **VTT/SRT parsing** (`subtitles.py`) — Timestamps stripped, HTML tags removed, word-level timing markers cleaned
5. **Deduplication** (`subtitles.py`) — Rolling-window overlap removal for auto-generated subs
6. **Chapter alignment** (`text.py`) — Single-pass O(n) merge of cues to chapter boundaries
7. **Text assembly** (`text.py`) — CJK-aware paragraph formation (no spaces for Chinese/Japanese)
8. **Markdown generation** (`markdown.py`) — YAML frontmatter, metadata blockquote, chapter sections

## Project Structure

```
yt_transcript.py              # CLI entry point (thin wrapper)
yt_transcript/
├── __init__.py               # Public API re-exports
├── __main__.py               # python3 -m yt_transcript support
├── models.py                 # Data classes (SubtitleCue, Chapter, VideoInfo, TranscriptResult)
├── exceptions.py             # Error hierarchy (YTTranscriptError + subclasses)
├── config.py                 # Constants and cookie config persistence
├── deps.py                   # Auto-install yt-dlp
├── ytdlp.py                  # yt-dlp subprocess interaction, URL resolution
├── metadata.py               # Parse yt-dlp JSON into typed data classes
├── subtitles.py              # Language selection, download, VTT/SRT parsing, dedup
├── text.py                   # CJK-aware paragraph assembly, chapter alignment
├── markdown.py               # Final Markdown document generation
├── output.py                 # Slugify, path generation, file writing
├── pipeline.py               # Single-video orchestration, dry-run
└── cli.py                    # Argument parsing and batch loop
```

## License

MIT
