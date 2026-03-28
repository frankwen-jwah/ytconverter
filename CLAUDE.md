# CLAUDE.md

## Project Overview

Content extraction pipeline. Extracts YouTube transcripts, web articles, PDF papers (especially arxiv), and local files (.md, .txt, .docx, .doc, .html), saving them as structured Markdown with sections. Supports member-only YouTube content, Whisper audio fallback, PDF layout analysis via pymupdf4llm, arXiv API metadata, local file format detection, and LLM-powered polish/summarize.

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

# PDF paper (arxiv or direct URL)
python3 yt_transcript.py "https://arxiv.org/abs/2301.07041"
python3 yt_transcript.py "https://example.com/paper.pdf"

# Local PDF file
python3 yt_transcript.py ./paper.pdf

# Local files (.md, .txt, .docx, .doc, .html)
python3 yt_transcript.py ./document.md
python3 yt_transcript.py ./report.docx
python3 yt_transcript.py ./notes.txt
python3 yt_transcript.py ./page.html

# Playlist or batch (mixed YouTube + articles + PDFs + local files)
python3 yt_transcript.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" "https://example.com/article" ./report.docx
python3 yt_transcript.py -f urls.txt

# Preview without downloading
python3 yt_transcript.py --dry-run "URL" ./file.docx
```

## Output

- Directory: `./content/`
- Filename: `YYYY-MM-DD_title-slug.md`
- Format: YAML frontmatter + `##` section headers + paragraph text
- YouTube outputs use `transcript.md`, articles use `article.md`, PDFs use `paper.md`, local files use `document.md`

## Dependencies

- Python 3.8+
- PyYAML (auto-installed on first run if missing)
- yt-dlp (auto-installed on first YouTube run if missing)
- requests (auto-installed on first article fetch if missing)
- trafilatura (auto-installed on first article extraction if missing)
- faster-whisper (auto-installed on first Whisper fallback if missing)
- pymupdf4llm (auto-installed on first PDF extraction if missing; includes PyMuPDF)
- python-docx (auto-installed on first .docx extraction if missing)
- mammoth (auto-installed on first .doc extraction if missing)
- Claude Code CLI (for --polish/--summarize; uses existing Claude subscription)

## Configuration

All settings are managed via `./content/config.yaml` — the single source of truth.

**Precedence**: CLI flags > config.yaml > builtin defaults

**Config sections**: `output`, `subtitles`, `auth`, `network`, `whisper`, `llm`, `text`, `flags`, `articles`, `pdf`, `local_files`, `urls`

On first run, a fully-commented `config.yaml` template is generated with all defaults. If a legacy `yt_transcripts/config.yaml` exists, it is auto-migrated to `content/config.yaml`.

The `Config` dataclass tree in `config.py` provides typed access throughout the pipeline. CLI uses `load_config()` → `apply_cli_overrides()` to build the final `Config` object.

## Architecture

Modular package (`yt_transcript/`) with these modules:

| Module | Responsibility |
|--------|---------------|
| `models.py` | Data classes: `SubtitleCue`, `Chapter`, `VideoInfo`, `TranscriptResult`, `ArticleSection`, `ArticleInfo`, `ArticleResult` |
| `exceptions.py` | Error hierarchy: `YTTranscriptError` + 11 subclasses |
| `config.py` | Config dataclasses, YAML loading, CLI override merging, migration |
| `deps.py` | Auto-install yt-dlp, PyYAML, requests, trafilatura, python-docx, mammoth if missing |
| `retry.py` | Shared retry-with-backoff utility (used by ytdlp and http_fetch) |
| `url_detect.py` | URL/file classification (YouTube, PDF, local file, article) |
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
| `arxiv.py` | ArXiv URL resolution, Atom API metadata fetch |
| `pdf.py` | PDF text extraction via pymupdf4llm, heading detection, section assembly |
| `pdf_pipeline.py` | Single-PDF orchestration, dry-run |
| `local_file.py` | Local file extraction (.md, .txt, .docx, .doc, .html) |
| `local_file_pipeline.py` | Single-local-file orchestration, dry-run |
| `cli.py` | Argument parsing + `main()` batch loop with URL/file dispatch |

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

### PDF Pipeline stages:
1. **URL classification** (`url_detect.py`) — detect arxiv or `.pdf` URLs, local PDF files
2. **ArXiv resolution** (`arxiv.py`, optional) — extract ID, fetch Atom API metadata (title, authors, abstract, categories)
3. **PDF download** (`http_fetch.py`) — binary fetch with retry/backoff, or read local file
4. **Layout-aware extraction** (`pdf.py`) — pymupdf4llm for two-column handling, tables, headings
5. **Section assembly** (`pdf.py`) — parse Markdown output into `ArticleSection` objects
6. **Abstract extraction** (`pdf.py`) — pull abstract out for frontmatter
7. **Math detection** (`pdf.py`) — flag presence of mathematical notation
8. **Markdown generation** (`markdown.py`) — YAML frontmatter with arxiv metadata, section body
9. **Polish** (`llm.py`, optional `--polish`) — same as other pipelines
10. **Summarize** (`llm.py`, optional `--summarize`) — same as other pipelines

### Local File Pipeline stages:
1. **File detection** (`url_detect.py`) — classify by extension (.md, .txt, .docx, .doc, .html, .htm)
2. **Format dispatch** (`local_file.py`) — route to per-format extractor
3. **Content extraction** (`local_file.py`) — .md: YAML frontmatter + heading parsing; .txt: paragraph splitting + pseudo-heading detection; .docx: python-docx style extraction; .doc: mammoth → HTML → trafilatura; .html: trafilatura
4. **Markdown generation** (`markdown.py`) — YAML frontmatter with file metadata, section body (reuses `build_article_markdown()`)
5. **Polish** (`llm.py`, optional `--polish`) — same as other pipelines
6. **Summarize** (`llm.py`, optional `--summarize`) — same as other pipelines

## Conventions

- URLs are auto-classified: YouTube domains → video pipeline, arxiv/`.pdf` URLs → PDF pipeline, everything else → article pipeline. Local files are detected by extension: `.pdf` → PDF pipeline, `.md`/`.txt`/`.docx`/`.doc`/`.html`/`.htm` → local file pipeline.
- Errors are classified: `VideoUnavailableError`, `AuthRequiredError`, `NoSubtitlesError`, `NetworkError`, `WhisperError`, `LLMError`, `ArticleFetchError`, `ContentExtractionError`, `PDFExtractionError`, `ArxivAPIError`, `LocalFileError`
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
