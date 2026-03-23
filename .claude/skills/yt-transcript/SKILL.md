---
name: yt-transcript
description: Extract YouTube video transcripts to structured Markdown with chapters, auto language detection, and member-only content support
user-invocable: false
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(python3 /workspace/yt_transcript.py *)
  - Bash(ls *)
---

# YouTube Transcript Extraction Skill

## What This Skill Does

Extracts transcripts from YouTube videos using yt-dlp and saves them as structured Markdown files. Supports:
- Member-only content via browser cookie authentication
- Auto-detection of video language (Chinese, English, Japanese, etc.)
- Chapter/section preservation as `##` headers
- Auto-generated and manual subtitle extraction
- Playlist and batch URL processing
- Optional Claude-based transcript polishing

## Pipeline

1. **yt-dlp** fetches video metadata (title, chapters, available subtitles)
2. Language auto-detected from video metadata; manual subs preferred over auto-generated
3. Subtitle file downloaded in VTT format to temp directory
4. VTT parsed: timestamps stripped, HTML tags removed, cues extracted
5. Auto-generated subs deduplicated (rolling-window overlap removal)
6. Cues aligned to chapter boundaries
7. Text assembled into paragraphs (CJK-aware: no-space joining for Chinese/Japanese)
8. Markdown generated with YAML frontmatter, chapter sections, metadata

## Script Location

`/workspace/yt_transcript.py`

## Output

- Directory: `./yt_transcripts/`
- Format: `YYYY-MM-DD_video-title-slug.md`

## Key Flags

| Flag | Purpose |
|------|---------|
| `--cookies-from-browser chrome` | Auth for member-only content |
| `--save-cookie-pref` | Remember cookie setting |
| `--lang CODE` | Force subtitle language |
| `--prefer-auto` | Prefer auto-generated subs |
| `--no-chapters` | Flat transcript, no sections |
| `--include-description` | Add video description |
| `--dry-run` | Preview without downloading |
| `--overwrite` | Replace existing files |
| `--polish` | Mark for Claude cleanup |
| `-o DIR` | Custom output directory |
| `-f FILE` | URL list file |

## Polish Mode

When `--polish` is used via the `/yt-transcript` command:
1. Script saves `.unpolished.md` files
2. Claude reads each unpolished file
3. Claude fixes punctuation, capitalization, speech-recognition errors
4. Original language preserved (no translation)
5. Polished version saved as final `.md`, unpolished removed
