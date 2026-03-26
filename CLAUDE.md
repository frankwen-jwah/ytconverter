# CLAUDE.md

## Project Overview

Content extraction pipeline. Extracts YouTube transcripts and web articles, saving them as structured Markdown with sections. Supports member-only YouTube content, Whisper audio fallback, and LLM-powered polish/summarize.

## Key Files

- `yt_transcript.py` — CLI entry point (thin wrapper, delegates to package)
- `yt_transcript/` — Main package (Python 3.8+, auto-installs dependencies)
- `content/config.yaml` — Configuration file (single source of truth for all defaults)
- `.claude/skills/yt-transcript/SKILL.md` — Claude skill definition
- `.claude/commands/yt-transcript.md` — `/yt-transcript` slash command

## How to Run

```bash
# YouTube video
python3 yt_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Web article
python3 yt_transcript.py "https://example.com/article"

# Member-only content
python3 yt_transcript.py --cookies-from-browser chrome "URL"

# Playlist or batch (mixed YouTube + articles)
python3 yt_transcript.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" "https://example.com/article"
python3 yt_transcript.py -f urls.txt

# Preview without downloading
python3 yt_transcript.py --dry-run "URL"
```

## Output

- Directory: `./content/`
- Filename: `YYYY-MM-DD_title-slug.md`
- Format: YAML frontmatter + `##` section headers + paragraph text
- YouTube outputs use `transcript.md`, articles use `article.md`

## Dependencies

- Python 3.8+
- PyYAML (auto-installed on first run if missing)
- yt-dlp (auto-installed on first YouTube run if missing)
- requests (auto-installed on first article fetch if missing)
- trafilatura (auto-installed on first article extraction if missing)
- faster-whisper (auto-installed on first Whisper fallback if missing)
- Claude Code CLI (for --polish/--summarize; uses existing Claude subscription)

## Configuration

All settings are managed via `./content/config.yaml` — the single source of truth.

**Precedence**: CLI flags > config.yaml > builtin defaults

**Config sections**: `output`, `subtitles`, `auth`, `network`, `whisper`, `llm`, `text`, `flags`, `articles`, `urls`

On first run, a fully-commented `config.yaml` template is generated with all defaults. If a legacy `yt_transcripts/config.yaml` exists, it is auto-migrated to `content/config.yaml`.

The `Config` dataclass tree in `config.py` provides typed access throughout the pipeline. CLI uses `load_config()` → `apply_cli_overrides()` to build the final `Config` object.

## Architecture

Modular package (`yt_transcript/`) with these modules:

| Module | Responsibility |
|--------|---------------|
| `models.py` | Data classes: `SubtitleCue`, `Chapter`, `VideoInfo`, `TranscriptResult`, `ArticleSection`, `ArticleInfo`, `ArticleResult` |
| `exceptions.py` | Error hierarchy: `YTTranscriptError` + 8 subclasses |
| `config.py` | Config dataclasses, YAML loading, CLI override merging, migration |
| `deps.py` | Auto-install yt-dlp, PyYAML, requests, trafilatura if missing |
| `retry.py` | Shared retry-with-backoff utility (used by ytdlp and http_fetch) |
| `url_detect.py` | URL classification (YouTube vs article) |
| `ytdlp.py` | yt-dlp subprocess interaction, URL resolution |
| `metadata.py` | Parse yt-dlp JSON into typed data classes |
| `subtitles.py` | Language selection, download, VTT/SRT parsing, dedup |
| `text.py` | CJK-aware paragraph assembly, chapter alignment |
| `http_fetch.py` | HTTP fetching with retry, UA rotation, SSL handling |
| `article.py` | Article content extraction via trafilatura |
| `article_pipeline.py` | Single-article orchestration, dry-run |
| `markdown.py` | Final Markdown generation (shared frontmatter helper) |
| `output.py` | Slugify, path generation, file writing |
| `whisper.py` | Whisper audio transcription fallback (auto-installs faster-whisper) |
| `llm.py` | Claude CLI polish & summarize (subprocess, auto-detects best model) |
| `pipeline.py` | Single-video orchestration, dry-run |
| `cli.py` | Argument parsing + `main()` batch loop with URL dispatch |

### YouTube Pipeline stages:
1. **yt-dlp metadata** (`ytdlp.py`) — fetch video info, chapters, available subtitle languages
2. **Language selection** (`subtitles.py`) — auto-detect from video metadata; prefer manual subs over auto-generated
3. **Subtitle download** (`subtitles.py`) — VTT format to temp directory; if no subs, falls back to Whisper audio transcription (`whisper.py`)
4. **VTT parsing** (`subtitles.py`) — strip timestamps, HTML tags, word-level timing markers
5. **Deduplication** (`subtitles.py`) — remove rolling-window overlaps in auto-generated subs
6. **Chapter alignment** (`text.py`) — single-pass O(n) merge of cues to chapter boundaries
7. **Text assembly** (`text.py`) — CJK-aware paragraph formation (no-space joining for Chinese/Japanese)
8. **Markdown generation** (`markdown.py`) — YAML frontmatter, metadata blockquote, chapter sections
9. **Polish** (`llm.py`, optional `--polish`) — Claude CLI fixes punctuation, speech-recognition errors, CJK formatting
10. **Summarize** (`llm.py`, optional `--summarize`) — Claude CLI generates Pyramid/SCQA summary

### Article Pipeline stages:
1. **HTTP fetch** (`http_fetch.py`) — download HTML with retry, UA rotation, SSL handling
2. **Content extraction** (`article.py`) — trafilatura XML extraction preserving headings
3. **Metadata extraction** (`article.py`) — title, author, date, site name, language
4. **Section assembly** (`article.py`) — headings → `ArticleSection` objects
5. **Markdown generation** (`markdown.py`) — YAML frontmatter, metadata, section body
6. **Polish** (`llm.py`, optional `--polish`) — same as YouTube
7. **Summarize** (`llm.py`, optional `--summarize`) — same as YouTube

## Conventions

- URLs are auto-classified: YouTube domains → video pipeline, everything else → article pipeline
- Errors are classified: `VideoUnavailableError`, `AuthRequiredError`, `NoSubtitlesError`, `NetworkError`, `WhisperError`, `LLMError`, `ArticleFetchError`, `ContentExtractionError`
- Network errors retry with exponential backoff via shared `retry.py` utility
- Batch processing: per-item errors are caught and logged, don't stop the batch
- All defaults stored in `./content/config.yaml` (CLI flags override)
- Filename collisions resolved by appending `-2`, `-3`, etc.

## Polish Mode

The `--polish` flag writes `.unpolished.md` files, then Claude CLI post-processes each section:
- Fix punctuation, capitalization, speech-recognition artifacts
- Preserve original language (no translation)
- CJK: fix segmentation and punctuation placement
- Auto-detects best available Claude model for summarize, second-best for polish (overridable with `--model` and `--polish-model`)

### Reprocessing Existing Outputs

```bash
# Polish + summarize an existing output folder (auto-detects transcript vs article)
python3 yt_transcript.py --reprocess path/to/output/folder --polish --summarize

# Summarize only, override model
python3 yt_transcript.py --reprocess folder1 folder2 --summarize --model sonnet

# Polish with a specific model
python3 yt_transcript.py --reprocess folder --polish --polish-model haiku
```
