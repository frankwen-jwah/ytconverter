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
- Whisper audio transcription fallback when no subtitles exist
- Playlist and batch URL processing
- Optional Claude-based transcript polishing
- Optional Pyramid/SCQA summary generation

## Pipeline

1. **yt-dlp** fetches video metadata (title, chapters, available subtitles)
2. Language auto-detected from video metadata; manual subs preferred over auto-generated
3. Subtitle file downloaded in VTT format to temp directory; if no subs, falls back to Whisper audio transcription
4. VTT parsed: timestamps stripped, HTML tags removed, cues extracted
5. Auto-generated subs deduplicated (rolling-window overlap removal)
6. Cues aligned to chapter boundaries
7. Text assembled into paragraphs (CJK-aware: no-space joining for Chinese/Japanese)
8. Markdown generated with YAML frontmatter, chapter sections, metadata

## Script Location

`/workspace/yt_transcript.py`

## Output

- Directory: `./yt_transcripts/`
- Folder: `YYYY-MM-DD_video-title-slug_YYYYMMDD-HHMM/`
- Files: `transcript.md`, optionally `summary.md`

## Key Flags

| Flag | Purpose |
|------|---------|
| `--cookies-from-browser chrome` | Auth for member-only content |
| `--cookies FILE` | Auth via Netscape cookies.txt file |
| `--lang CODE` | Force subtitle language |
| `--prefer-auto` | Prefer auto-generated subs |
| `--no-chapters` | Flat transcript, no sections |
| `--include-description` | Add video description |
| `--dry-run` | Preview without downloading |
| `--overwrite` | Replace existing files |
| `--polish` | Mark for Claude cleanup |
| `--summarize` | Generate Pyramid/SCQA summary |
| `--no-whisper` | Disable Whisper audio fallback |
| `--whisper-model MODEL` | Whisper model size (default: base) |
| `-o DIR` | Custom output directory |
| `-f FILE` | URL list file |

## Polish Mode

When `--polish` is used via the `/yt-transcript` command:
1. Script saves `transcript.unpolished.md` in output folder
2. Claude reads each unpolished file
3. Claude fixes punctuation, capitalization, speech-recognition errors
4. Original language preserved (no translation)
5. Update frontmatter: change `polished: false` to `polished: true`
6. Polished version saved as `transcript.md`, unpolished removed

## Summarize Mode

When `--summarize` is used via the `/yt-transcript` command:
1. After polish (or directly after extraction if no polish), Claude reads `transcript.md`
2. Summary written in the same language as the transcript
3. Uses Pyramid Principle (governing thought → key points) and SCQA framework
4. Output: `summary.md` in the same folder as `transcript.md`
