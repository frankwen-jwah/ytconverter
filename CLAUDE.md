# CLAUDE.md

## Project Overview

YouTube transcript extraction pipeline. Extracts subtitles/transcripts from YouTube videos (including member-only content) and saves them as structured Markdown with chapter sections.

## Key Files

- `yt_transcript.py` — CLI entry point (thin wrapper, delegates to package)
- `yt_transcript/` — Main package (Python 3.8+, stdlib only, auto-installs yt-dlp)
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

Modular package (`yt_transcript/`) with these modules:

| Module | Responsibility |
|--------|---------------|
| `models.py` | Data classes: `SubtitleCue`, `Chapter`, `VideoInfo`, `TranscriptResult` |
| `exceptions.py` | Error hierarchy: `YTTranscriptError` + 4 subclasses |
| `config.py` | Constants (`OUTPUT_DIR`, `CONFIG_FILE`) + cookie persistence |
| `deps.py` | Auto-install yt-dlp if missing |
| `ytdlp.py` | yt-dlp subprocess interaction, URL resolution |
| `metadata.py` | Parse yt-dlp JSON into typed data classes |
| `subtitles.py` | Language selection, download, VTT/SRT parsing, dedup |
| `text.py` | CJK-aware paragraph assembly, chapter alignment |
| `markdown.py` | Final Markdown document generation |
| `output.py` | Slugify, path generation, file writing |
| `pipeline.py` | Single-video orchestration, dry-run |
| `cli.py` | Argument parsing + `main()` batch loop |

Pipeline stages:
1. **yt-dlp metadata** (`ytdlp.py`) — fetch video info, chapters, available subtitle languages
2. **Language selection** (`subtitles.py`) — auto-detect from video metadata; prefer manual subs over auto-generated
3. **Subtitle download** (`subtitles.py`) — VTT format to temp directory
4. **VTT parsing** (`subtitles.py`) — strip timestamps, HTML tags, word-level timing markers
5. **Deduplication** (`subtitles.py`) — remove rolling-window overlaps in auto-generated subs
6. **Chapter alignment** (`text.py`) — single-pass O(n) merge of cues to chapter boundaries
7. **Text assembly** (`text.py`) — CJK-aware paragraph formation (no-space joining for Chinese/Japanese)
8. **Markdown generation** (`markdown.py`) — YAML frontmatter, metadata blockquote, chapter sections

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
