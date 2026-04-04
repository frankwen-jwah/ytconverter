---
name: content-extractor
description: Extract content from YouTube videos, web articles, PDFs, local files, podcasts, and tweets to structured Markdown
user-invocable: false
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(python3 /workspace/content_extractor.py *)
  - Bash(ls *)
---

# Content Extraction Skill

## What This Skill Does

Extracts content from multiple source types and saves as structured Markdown files. Supports:
- YouTube videos (subtitles, Whisper fallback, chapters, member-only content)
- Web articles via trafilatura
- PDF papers (arxiv, layout-aware extraction)
- Local files (.md, .txt, .docx, .doc, .html, .pptx)
- Podcasts (RSS feeds, platform URLs, Whisper transcription)
- X/Twitter posts, threads, note tweets, and X Articles
- Playlist and batch URL processing
- Optional Claude-based polishing and Pyramid/SCQA summary generation

## Script Location

`/workspace/content_extractor.py`

## Output

- Directory: `./content/output/`
- Folder: `YYYY-MM-DD_title-slug/`
- Files: `transcript.md`, `article.md`, `paper.md`, `document.md`, `presentation.md`, `tweet.md`, `podcast.md`, optionally `summary.md`

## Key Flags

| Flag | Purpose |
|------|---------|
| `--cookies-from-browser chrome` | Auth for member-only content |
| `--cookies FILE` | Auth via Netscape cookies.txt file |
| `--lang CODE` | Force subtitle language |
| `--prefer-auto` | Prefer auto-generated subs |
| `--no-chapters` | Flat transcript, no sections |
| `--include-description` | Add video/article description |
| `--dry-run` | Preview without downloading |
| `--overwrite` | Replace existing files |
| `--polish` | Claude cleanup |
| `--summarize` | Generate Pyramid/SCQA summary |
| `--no-whisper` | Disable Whisper audio fallback |
| `--whisper-model MODEL` | Whisper model size (default: base) |
| `--max-episodes N` | Max podcast episodes to extract |
| `--nitter-instance HOST` | Nitter instance for tweet extraction |
| `--no-speaker-notes` | Exclude PowerPoint speaker notes |
| `-o DIR` | Custom output directory |
| `-f FILE` | URL list file |

## Polish Mode

When `--polish` is used:
1. Script saves `{basename}.unpolished.md` in output folder
2. Claude fixes punctuation, capitalization, speech-recognition errors
3. Original language preserved (no translation)
4. Polished version saved as `{basename}.md`

## Summarize Mode

When `--summarize` is used:
1. After polish (or directly after extraction), Claude reads the content file
2. Summary written in the same language as the content
3. Uses Pyramid Principle (governing thought → key points) and SCQA framework
4. Output: `summary.md` in the same folder
