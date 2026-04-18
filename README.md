# Content Extraction Pipeline

Extract and convert YouTube transcripts, web articles, PDF papers (arXiv), local files (.md, .txt, .docx, .doc, .html, .mhtml, .pptx), podcast episodes, and X/Twitter posts into structured Markdown with sections.

## Features

- **YouTube transcripts** -- Chapter-aware extraction with auto language detection, member-only support, manual & auto-generated subs, Whisper audio fallback, CJK-aware formatting
- **Web articles** -- Trafilatura-based extraction preserving headings, tables, and metadata
- **PDF papers** -- Layout-aware extraction via opendataloader-pdf with arXiv API metadata, two-column support, math detection
- **Local files** -- Extract content from .md, .txt, .docx, .doc, .html, .mhtml, and .pptx files on disk (MHTML web archive decoding, PowerPoint slides with speaker notes and table support)
- **Podcast episodes** -- RSS feed parsing with feedparser, audio download + Whisper transcription, episode metadata extraction; supports Apple Podcasts, Spotify, and generic RSS feeds
- **X/Twitter posts** -- Tweet extraction via syndication API (no auth), oEmbed fallback, Nitter last resort; note tweet (long tweet) full-text recovery; X Article extraction via DraftJS block parsing + Playwright; link-only tweet auto-extraction; t.co URL expansion; tweet subtype classification (tweet/note_tweet/x_article)
- **Batch processing** -- Mix URLs and local files in a single run; playlists, channels, podcast feeds, URL files
- **Auto-polish (speech-to-text)** -- Fix punctuation, speech-recognition artifacts, CJK formatting
- **Image description** -- Azure OpenAI vision describes images from PDFs, slides, articles, and tweets inline (on by default; use `--no-images` to disable)

## Requirements

- Python 3.10+
- PyYAML (auto-installed)
- yt-dlp (auto-installed on first YouTube run)
- requests (auto-installed on first article/PDF fetch)
- trafilatura (auto-installed on first article extraction)
- faster-whisper (auto-installed on first Whisper fallback)
- opendataloader-pdf (auto-installed on first PDF extraction; requires Java 11+)
- python-docx (auto-installed on first .docx extraction)
- python-pptx (auto-installed on first .pptx extraction)
- mammoth (auto-installed on first .doc extraction)
- feedparser (auto-installed on first podcast RSS feed parsing)
- beautifulsoup4 (auto-installed on first tweet extraction)
- playwright (auto-installed on first X Article extraction; downloads Chromium ~170MB)
- openai (auto-installed for Azure OpenAI backend)
- python-dotenv (auto-installed for .env credential loading)
- markitdown[all] (auto-installed for MarkItDown file conversion)

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
python3 content_extractor.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Web article
python3 content_extractor.py "https://example.com/article"

# PDF paper (arXiv or direct URL)
python3 content_extractor.py "https://arxiv.org/abs/2301.07041"

# Local files
python3 content_extractor.py ./document.md
python3 content_extractor.py ./report.docx
python3 content_extractor.py ./notes.txt
python3 content_extractor.py ./slides.pptx

# Podcast (RSS feed or platform URL)
python3 content_extractor.py "https://feeds.example.com/podcast.xml"
python3 content_extractor.py --max-episodes 3 "https://podcasts.apple.com/us/podcast/show/id123"

# X/Twitter post or thread
python3 content_extractor.py "https://x.com/user/status/123456789"

# Mixed batch
python3 content_extractor.py ./doc.md "https://youtube.com/watch?v=ID" ./paper.pdf "https://example.com/article"

# Preview without extracting
python3 content_extractor.py --dry-run "URL" ./file.docx
```

Output is saved to `./content/YYYY-MM-DD_title-slug/`.

## Usage

```
python3 content_extractor.py [OPTIONS] [URLs/files...]
```

### Input Options

| Option | Description |
|--------|-------------|
| `URLs/files...` | YouTube URLs, article URLs, PDF URLs, podcast feeds, tweet URLs, or local file paths (.md, .txt, .docx, .doc, .html, .mhtml, .pptx, .pdf) |
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
| `--no-images` | Disable image description via Azure OpenAI vision |
| `--no-whisper` | Disable Whisper audio transcription fallback |
| `--whisper-model MODEL` | Whisper model size: tiny, base, small, medium, large-v3 |
| `--whisper-device DEVICE` | Whisper device: auto, cuda, cpu |
| `--polish-model MODEL` | Override Azure OpenAI deployment for auto-polish |

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

### PowerPoint-specific

| Option | Description |
|--------|-------------|
| `--no-speaker-notes` | Exclude speaker notes from PowerPoint extraction |

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
4. Run: `python3 content_extractor.py --cookies cookies.txt "https://x.com/user/status/..."`

Cookies last ~1 year. Re-export only if you log out or get auth errors. Without `--cookies`, X Articles output a preview-only extract.

## Examples

```bash
# Member-only YouTube content
python3 content_extractor.py --cookies-from-browser chrome "https://www.youtube.com/watch?v=MEMBER_VIDEO"

# Playlist
python3 content_extractor.py "https://www.youtube.com/playlist?list=PLxxxxxxx"

# Batch from URL file
python3 content_extractor.py -f urls.txt

# arXiv paper
python3 content_extractor.py "https://arxiv.org/abs/2301.07041"

# Local Word document with polish

# PowerPoint presentation (exclude speaker notes)
python3 content_extractor.py --no-speaker-notes ./slides.pptx

# Local Markdown + article in one batch
python3 content_extractor.py ./notes.md "https://example.com/article"

# Podcast feed (latest 5 episodes)
python3 content_extractor.py --max-episodes 5 "https://feeds.example.com/podcast.xml"

# X/Twitter post (no auth needed for regular tweets)
python3 content_extractor.py "https://x.com/user/status/123456789"

# X Article (requires cookies.txt for full content)
python3 content_extractor.py --cookies cookies.txt "https://x.com/user/status/123456789"

# Tweet with custom Nitter instance (last-resort fallback, supports threads)
python3 content_extractor.py --nitter-instance nitter.net "https://twitter.com/user/status/123"

# Disable image description
python3 content_extractor.py --no-images ./paper.pdf

# Reprocess existing output
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
  include_speaker_notes: true
podcast:
  enabled: true
  max_episodes: 10
  prefer_rss: true
twitter:
  enabled: true
  nitter_instance: "nitter.poast.org"
vision:
  enabled: true
urls: []
```

See the generated `config.yaml` for all available options with comments.

## Output Format

Each extracted item creates a folder in `./content/` with:

- `transcript.md` / `article.md` / `paper.md` / `document.md` / `presentation.md` / `podcast.md` / `tweet.md` -- Main output
- `*.unpolished.md` -- Pre-polish version (auto-saved for speech-to-text content)

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

Content types: `transcript` (YouTube), `article` (web), `paper` (PDF), `document` (local files), `presentation` (PowerPoint), `podcast` (podcast episodes), `tweet` (X/Twitter posts).

Tweet outputs may include `tweet_subtype` in frontmatter: `note_tweet` (long tweets by premium users) or `x_article` (X Article pages). Regular tweets omit this field.

## Supported Local File Formats

| Format | Dependencies | Extraction Method |
|--------|-------------|-------------------|
| `.md` | None | YAML frontmatter + Markdown heading parsing |
| `.txt` | None | Paragraph splitting, optional pseudo-heading detection |
| `.docx` | python-docx (auto-installed) | Paragraph/heading style extraction, table support, inline-image extraction for vision |
| `.doc` | mammoth (auto-installed) | Convert to HTML, then trafilatura extraction |
| `.pptx` | python-pptx (auto-installed) | Slide text, tables, speaker notes, and picture-shape image extraction for vision (each slide → section) |
| `.html`/`.htm` | trafilatura (auto-installed) | Same extraction as web articles |
| `.mhtml`/`.mht` | None (stdlib `email`) | MIME decode → trafilatura extraction |
| `.pdf` | opendataloader-pdf (auto-installed) | Layout-aware extraction, arXiv metadata |

## Claude Integration

When used with [Claude Code](https://claude.com/claude-code):

- **`/content-extractor <URL>`** -- Slash command that runs the pipeline and optionally polishes output
- **Auto-polish** -- Speech-to-text outputs automatically polished via Azure OpenAI

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
3. **Layout extraction** -- opendataloader-pdf for two-column, tables, headings, and embedded-image extraction (MarkItDown is not used for PDFs; its pdfminer backend drops images)
4. **Image description** -- Azure OpenAI vision describes each embedded figure in place (disable with `--no-images`)
5. **Markdown generation** -- YAML frontmatter with paper metadata

### Local File Pipeline
1. **File detection** -- Classify by extension (.md, .txt, .docx, .doc, .html, .mhtml, .pptx)
2. **Format-specific extraction** -- Per-format parser producing structured sections (.pptx: slides with speaker notes and tables)
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
content_extractor.py                # CLI entry point (thin wrapper)
content_extractor/
+-- __init__.py                 # Public API re-exports
+-- __main__.py                 # python3 -m content_extractor support
+-- models.py                   # Data classes (VideoInfo, ArticleInfo, PDFInfo, etc.)
+-- exceptions.py               # Error hierarchy (PipelineError + subclasses)
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
+-- pdf.py                      # PDF text extraction via opendataloader-pdf
+-- pdf_pipeline.py             # Single-PDF orchestration
+-- local_file.py               # Local file extraction (.md, .txt, .docx, .doc, .html, .mhtml, .pptx)
+-- local_file_pipeline.py      # Single-local-file orchestration
+-- podcast.py                  # Podcast RSS feed parsing, episode metadata
+-- podcast_pipeline.py         # Single-podcast-episode orchestration, feed resolution
+-- tweet.py                    # Twitter/X extraction via syndication API, oEmbed, Playwright, Nitter
+-- tweet_pipeline.py           # Single-tweet orchestration
+-- markdown.py                 # Final Markdown generation (shared frontmatter helper)
+-- output.py                   # Slugify, path generation, file writing
+-- whisper.py                  # Whisper audio transcription fallback
+-- llm.py                      # LLM-based polish via Azure OpenAI
+-- llm_backend.py              # Unified Azure OpenAI backend with rate limiting
+-- rate_limiter.py             # Proactive rate limiter (sliding-window TPM/RPM)
+-- markitdown_bridge.py        # MarkItDown converter (text-only Office: .xlsx/.csv/.json/.xml/.msg/.epub)
+-- pipeline.py                 # Single-video orchestration
+-- cli.py                      # Argument parsing and batch loop with URL/file dispatch
content/
+-- config.yaml                 # Configuration file (single source of truth)
```

## License

MIT
