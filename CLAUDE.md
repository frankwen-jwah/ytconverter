# CLAUDE.md

## Project Overview

YouTube transcript extraction pipeline. Extracts subtitles/transcripts from YouTube videos (including member-only content) and saves them as structured Markdown with chapter sections.

## Key Files

- `yt_transcript.py` — Main pipeline script (Python 3.8+, stdlib only, auto-installs yt-dlp)
- `.claude/skills/yt-transcript/SKILL.md` — Claude skill definition
- `.claude/commands/yt-transcript.md` — `/yt-transcript` slash command

## How to Run

```bash
# Single video
python3 yt_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Member-only content
python3 yt_transcript.py --cookies-from-browser chrome "URL"

# Playlist or batch
python3 yt_transcript.py "https://www.youtube.com/playlist?list=PLAYLIST_ID"
python3 yt_transcript.py -f urls.txt

# Preview available subs without downloading
python3 yt_transcript.py --dry-run "URL"
```

## Output

- Directory: `./yt_transcripts/`
- Filename: `YYYY-MM-DD_video-title-slug.md`
- Format: YAML frontmatter + `##` chapter headers + paragraph text

## Dependencies

- Python 3.8+ (stdlib only)
- yt-dlp (auto-installed on first run if missing)

## Architecture

Single-file pipeline with these stages:
1. **yt-dlp metadata** — fetch video info, chapters, available subtitle languages
2. **Language selection** — auto-detect from video metadata; prefer manual subs over auto-generated
3. **Subtitle download** — VTT format to temp directory
4. **VTT parsing** — strip timestamps, HTML tags, word-level timing markers
5. **Deduplication** — remove rolling-window overlaps in auto-generated subs
6. **Chapter alignment** — single-pass O(n) merge of cues to chapter boundaries
7. **Text assembly** — CJK-aware paragraph formation (no-space joining for Chinese/Japanese)
8. **Markdown generation** — YAML frontmatter, metadata blockquote, chapter sections

## Conventions

- Errors are classified by yt-dlp stderr patterns: `VideoUnavailableError`, `AuthRequiredError`, `NoSubtitlesError`, `NetworkError`
- Network errors retry with exponential backoff (1s, 2s, 4s)
- Batch processing: per-video errors are caught and logged, don't stop the batch
- Cookie preferences persist in `./yt_transcripts/.config.json`
- Filename collisions resolved by appending `-2`, `-3`, etc.

## Polish Mode

The `--polish` flag writes `.unpolished.md` files. When invoked via `/yt-transcript`, Claude post-processes each section:
- Fix punctuation, capitalization, speech-recognition artifacts
- Preserve original language (no translation)
- CJK: fix segmentation and punctuation placement
