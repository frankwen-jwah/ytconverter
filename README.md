# Content Extraction Pipeline

Extract and convert YouTube transcripts, web articles, PDF papers (arXiv), local files (.md, .txt, .docx, .doc, .html), podcast episodes, and X/Twitter posts into structured Markdown with sections.

## Features

- **YouTube transcripts** -- Chapter-aware extraction with auto language detection, member-only support, manual & auto-generated subs, Whisper audio fallback, CJK-aware formatting
- **Web articles** -- Trafilatura-based extraction preserving headings, tables, and metadata
- **PDF papers** -- Layout-aware extraction via pymupdf4llm with arXiv API metadata, two-column support, math detection
- **Local files** -- Extract content from .md, .txt, .docx, .doc, and .html files on disk
- **Podcast episodes** -- RSS feed parsing with feedparser, audio download + Whisper transcription, episode metadata extraction; supports Apple Podcasts, Spotify, and generic RSS feeds
- **X/Twitter posts** -- Tweet extraction via syndication API (no auth), oEmbed fallback, Nitter last resort; note tweet (long tweet) full-text recovery; X Article extraction via DraftJS block parsing + Playwright; link-only tweet auto-extraction; t.co URL expansion; tweet subtype classification (tweet/note_tweet/x_article)
- **Batch processing** -- Mix URLs and local files in a single run; playlists, channels, podcast feeds, URL files
- **Optional Claude polish** -- Fix punctuation, speech-recognition artifacts, CJK formatting
- **Pyramid/SCQA summary** -- Generate structured summaries via Claude CLI

## Requirements

- Python 3.8+
- PyYAML (auto-installed)
- yt-dlp (auto-installed on first YouTube run)
- requests (auto-installed on first article/PDF fetch)
- trafilatura (auto-installed on first article extraction)
- faster-whisper (auto-installed on first Whisper fallback)
- pymupdf4llm (auto-installed on first PDF extraction)
- python-docx (auto-installed on first .docx extraction)
- mammoth (auto-installed on first .doc extraction)
- feedparser (auto-installed on first podcast RSS feed parsing)
- beautifulsoup4 (auto-installed on first tweet extraction)
- playwright (auto-installed on first X Article extraction; downloads Chromium ~170MB)
- Claude Code CLI (for `--polish`/`--summarize`; uses existing Claude subscription)

### Install all dependencies at once

```bash
pip install -r requirements.txt
```

### GPU acceleration (optional)

Whisper transcription uses CPU by default. For NVIDIA GPU acceleration:

```bash
pip install faster-whisper[cuda]
```

## Quick Start

```bash
# YouTube video
python3 yt_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Web article
python3 yt_transcript.py "https://example.com/article"

# PDF paper (arXiv or direct URL)
python3 yt_transcript.py "https://arxiv.org/abs/2301.07041"

# Local files
python3 yt_transcript.py ./document.md
python3 yt_transcript.py ./report.docx
python3 yt_transcript.py ./notes.txt

# Podcast (RSS feed or platform URL)
python3 yt_transcript.py "https://feeds.example.com/podcast.xml"
python3 yt_transcript.py --max-episodes 3 "https://podcasts.apple.com/us/podcast/show/id123"

# X/Twitter post or thread
python3 yt_transcript.py "https://x.com/user/status/123456789"

# Mixed batch
python3 yt_transcript.py ./doc.md "https://youtube.com/watch?v=ID" ./paper.pdf "https://example.com/article"

# Preview without extracting
python3 yt_transcript.py --dry-run "URL" ./file.docx
```

Output is saved to `./content/YYYY-MM-DD_title-slug/`.

## Usage

```
python3 yt_transcript.py [OPTIONS] [URLs/files...]
```

### Input Options

| Option | Description |
|--------|-------------|
| `URLs/files...` | YouTube URLs, article URLs, PDF URLs, podcast feeds, tweet URLs, or local file paths (.md, .txt, .docx, .doc, .html, .pdf) |
| `-f, --file FILE` | Text file with one URL/path per line |

### Authentication (YouTube, X Articles)

| Option | Description |
|--------|-------------|
| `--cookies-from-browser BROWSER` | Extract cookies from browser (chrome, firefox, edge, safari, opera, brave). Used for YouTube member-only content and podcast audio. |
| `--cookies FILE` | Path to Netscape-format cookies.txt file. Required for X Article full-content extraction (see below). |

### Language (YouTube)

| Option | Description |
|--------|-------------|
| `--lang CODE` | Force subtitle language (e.g. `en`, `zh-Hans`, `ja`) |
| `--prefer-auto` | Prefer auto-generated subs over manual |

### Output

| Option | Description |
|--------|-------------|
| `-o, --output-dir DIR` | Output directory (default: `./content/`) |
| `--no-chapters` | Ignore chapter markers, output flat transcript |
| `--include-description` | Include video/article description in output |
| `--overwrite` | Overwrite existing files |

### Behavior

| Option | Description |
|--------|-------------|
| `--dry-run` | Show info without downloading/extracting |
| `--retries N` | Retry attempts for network errors (default: 3) |
| `--polish` | Polish output via Claude CLI (fix punctuation, artifacts) |
| `--summarize` | Generate Pyramid/SCQA summary via Claude CLI |
| `--no-whisper` | Disable Whisper audio transcription fallback |
| `--whisper-model MODEL` | Whisper model size: tiny, base, small, medium, large-v3 |
| `--whisper-device DEVICE` | Whisper device: auto, cuda, cpu |
| `--model MODEL` | Claude model alias (opus, sonnet, haiku) for summarize |
| `--polish-model MODEL` | Claude model for polishing |
| `--reprocess FOLDER...` | Re-run polish/summarize on existing output folder(s) |

### PDF-specific

| Option | Description |
|--------|-------------|
| `--no-abstract` | Exclude abstract from PDF paper output |
| `--strip-references` | Strip References/Bibliography section from PDF papers |
| `--max-pages N` | Maximum pages to extract from PDF (0 = unlimited) |

### Podcast-specific

| Option | Description |
|--------|-------------|
| `--max-episodes N` | Maximum episodes to extract from a podcast feed (0 = unlimited) |

### Twitter/X-specific

| Option | Description |
|--------|-------------|
| `--nitter-instance HOST` | Nitter instance hostname for tweet extraction (last-resort fallback) |
| `--cookies FILE` | Required for X Article full content. Export from Chrome (see below). |

#### Exporting cookies for X Articles

X Articles are JS-rendered pages that require authentication. To extract full content:

1. Open Chrome and go to `x.com` (make sure you're logged in)
2. Install the **"Get cookies.txt LOCALLY"** browser extension
3. Navigate to any `x.com` page, click the extension, and export as `cookies.txt`
4. Run: `python3 yt_transcript.py --cookies cookies.txt "https://x.com/user/status/..."`

Cookies last ~1 year. Re-export only if you log out or get auth errors. Without `--cookies`, X Articles output a preview-only extract.

## Examples

```bash
# Member-only YouTube content
python3 yt_transcript.py --cookies-from-browser chrome "https://www.youtube.com/watch?v=MEMBER_VIDEO"

# Playlist
python3 yt_transcript.py "https://www.youtube.com/playlist?list=PLxxxxxxx"

# Batch from URL file
python3 yt_transcript.py -f urls.txt

# arXiv paper
python3 yt_transcript.py "https://arxiv.org/abs/2301.07041"

# Local Word document with polish
python3 yt_transcript.py --polish ./report.docx

# Local Markdown + article in one batch
python3 yt_transcript.py ./notes.md "https://example.com/article"

# Podcast feed (latest 5 episodes)
python3 yt_transcript.py --max-episodes 5 "https://feeds.example.com/podcast.xml"

# X/Twitter post (no auth needed for regular tweets)
python3 yt_transcript.py "https://x.com/user/status/123456789"

# X Article (requires cookies.txt for full content)
python3 yt_transcript.py --cookies cookies.txt "https://x.com/user/status/123456789"

# Tweet with custom Nitter instance (last-resort fallback, supports threads)
python3 yt_transcript.py --nitter-instance nitter.net "https://twitter.com/user/status/123"

# Reprocess existing output
python3 yt_transcript.py --reprocess content/2026-03-15_video-title/ --polish --summarize
```

## Configuration

All defaults are managed via `./content/config.yaml` (created on first run). CLI flags always override config values.

```yaml
# Key sections:
output:
  dir: "./content"
  slug_max_length: 80
subtitles:
  lang: null
  prefer_auto: false
auth:
  cookies_from_browser: null
whisper:
  enabled: true
  model: "large-v3"
articles:
  enabled: true
  include_tables: true
pdf:
  enabled: true
  include_abstract: true
local_files:
  enabled: true
  include_tables: true
  detect_txt_headings: true
podcast:
  enabled: true
  max_episodes: 10
  prefer_rss: true
twitter:
  enabled: true
  nitter_instance: "nitter.poast.org"
urls: []
```

See the generated `config.yaml` for all available options with comments.

## Output Format

Each extracted item creates a folder in `./content/` with:

- `transcript.md` / `article.md` / `paper.md` / `document.md` / `podcast.md` / `tweet.md` -- Main output
- `*.unpolished.md` -- Pre-polish version (when `--polish` used)
- `summary.md` -- Structured summary (when `--summarize` used)

### Frontmatter example

```yaml
---
title: "Document Title"
url: "file:///path/to/file.docx"
author: "Author Name"
date: "2026-01-15"
language: "en"
word_count: 1234
content_type: "document"
---
```

Content types: `transcript` (YouTube), `article` (web), `paper` (PDF), `document` (local files), `podcast` (podcast episodes), `tweet` (X/Twitter posts).

Tweet outputs may include `tweet_subtype` in frontmatter: `note_tweet` (long tweets by premium users) or `x_article` (X Article pages). Regular tweets omit this field.

## Supported Local File Formats

| Format | Dependencies | Extraction Method |
|--------|-------------|-------------------|
| `.md` | None | YAML frontmatter + Markdown heading parsing |
| `.txt` | None | Paragraph splitting, optional pseudo-heading detection |
| `.docx` | python-docx (auto-installed) | Paragraph/heading style extraction, table support |
| `.doc` | mammoth (auto-installed) | Convert to HTML, then trafilatura extraction |
| `.html`/`.htm` | trafilatura (auto-installed) | Same extraction as web articles |
| `.pdf` | pymupdf4llm (auto-installed) | Layout-aware extraction, arXiv metadata |

## Claude Integration

When used with [Claude Code](https://claude.com/claude-code):

- **`/yt-transcript <URL>`** -- Slash command that runs the pipeline and optionally polishes output
- **`--polish` flag** -- Saves `.unpolished.md` first, then Claude CLI fixes artifacts
- **`--summarize` flag** -- Generates a Pyramid/SCQA structured summary
- **`--reprocess`** -- Re-run polish/summarize on existing output folders

## How It Works

### YouTube Pipeline
1. **Metadata fetch** -- yt-dlp fetches video info, chapters, subtitle tracks
2. **Language selection** -- Auto-detected; manual subs preferred over auto-generated
3. **Subtitle download** -- WebVTT format, VTT/SRT parsing, deduplication
4. **Chapter alignment** -- Single-pass O(n) merge of cues to chapter boundaries
5. **Text assembly** -- CJK-aware paragraph formation
6. **Markdown generation** -- YAML frontmatter, metadata, chapter sections

### Article Pipeline
1. **HTTP fetch** -- Download with retry, UA rotation, SSL handling
2. **Content extraction** -- Trafilatura XML extraction preserving headings
3. **Markdown generation** -- YAML frontmatter, metadata, sections

### PDF Pipeline
1. **URL classification** -- Detect arXiv or direct PDF URLs
2. **ArXiv metadata** -- Fetch via Atom API (title, authors, abstract, categories)
3. **Layout extraction** -- pymupdf4llm for two-column, tables, headings
4. **Markdown generation** -- YAML frontmatter with paper metadata

### Local File Pipeline
1. **File detection** -- Classify by extension (.md, .txt, .docx, .doc, .html)
2. **Format-specific extraction** -- Per-format parser producing structured sections
3. **Markdown generation** -- Same output format as articles

### Podcast Pipeline
1. **Feed resolution** -- Parse RSS feed via feedparser or yt-dlp for platform URLs
2. **Audio download** -- Reuses yt-dlp audio download from YouTube pipeline
3. **Whisper transcription** -- Reuses faster-whisper transcription
4. **Text assembly** -- CJK-aware paragraph formation (same as YouTube)
5. **Markdown generation** -- YAML frontmatter with episode metadata + transcript

### Twitter/X Pipeline
1. **URL normalization** -- Normalize twitter.com/x.com/nitter URLs, extract tweet ID
2. **Cascade fetch** -- Syndication API (primary, free) -> oEmbed (fallback) -> Nitter (last resort for threads)
3. **Note tweet detection** -- Long tweets (note_tweets) are truncated by the API; full text recovered via nested API keys or Playwright fallback
4. **X Article extraction** -- If tweet links to an X Article and `--cookies` provided, Playwright renders the page, scrolls to load lazy content, then extracts via DraftJS block parsing (headings, ordered/unordered lists, paragraphs). Falls back to trafilatura if DraftJS fails. Without cookies, outputs preview-only.
5. **t.co link expansion** -- Resolve shortened URLs via HEAD requests (syndication/oEmbed paths)
6. **Link-only extraction** -- If tweet is just a URL to an external site, extract article content via trafilatura
7. **Markdown generation** -- YAML frontmatter with tweet metadata and `tweet_subtype` (note_tweet/x_article when applicable)

## Project Structure

```
yt_transcript.py                # CLI entry point (thin wrapper)
yt_transcript/
+-- __init__.py                 # Public API re-exports
+-- __main__.py                 # python3 -m yt_transcript support
+-- models.py                   # Data classes (VideoInfo, ArticleInfo, PDFInfo, etc.)
+-- exceptions.py               # Error hierarchy (YTTranscriptError + subclasses)
+-- config.py                   # Config dataclasses, YAML loading, CLI override merging
+-- deps.py                     # Auto-install dependencies (yt-dlp, trafilatura, etc.)
+-- retry.py                    # Shared retry-with-backoff utility
+-- url_detect.py               # URL/file classification (YouTube, PDF, podcast, tweet, article, local file)
+-- ytdlp.py                    # yt-dlp subprocess interaction, URL resolution
+-- metadata.py                 # Parse yt-dlp JSON into typed data classes
+-- subtitles.py                # Language selection, download, VTT/SRT parsing, dedup
+-- text.py                     # CJK-aware paragraph assembly, chapter alignment
+-- http_fetch.py               # HTTP fetching with retry, UA rotation, SSL handling
+-- article.py                  # Article content extraction via trafilatura
+-- article_pipeline.py         # Single-article orchestration
+-- arxiv.py                    # ArXiv URL resolution, Atom API metadata fetch
+-- pdf.py                      # PDF text extraction via pymupdf4llm
+-- pdf_pipeline.py             # Single-PDF orchestration
+-- local_file.py               # Local file extraction (.md, .txt, .docx, .doc, .html)
+-- local_file_pipeline.py      # Single-local-file orchestration
+-- podcast.py                  # Podcast RSS feed parsing, episode metadata
+-- podcast_pipeline.py         # Single-podcast-episode orchestration, feed resolution
+-- tweet.py                    # Twitter/X extraction via syndication API, oEmbed, Playwright, Nitter
+-- tweet_pipeline.py           # Single-tweet orchestration
+-- markdown.py                 # Final Markdown generation (shared frontmatter helper)
+-- output.py                   # Slugify, path generation, file writing
+-- whisper.py                  # Whisper audio transcription fallback
+-- llm.py                      # Claude CLI polish & summarize
+-- pipeline.py                 # Single-video orchestration
+-- cli.py                      # Argument parsing and batch loop with URL/file dispatch
content/
+-- config.yaml                 # Configuration file (single source of truth)
```

## License

MIT
