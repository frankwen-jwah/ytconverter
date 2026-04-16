# CLAUDE.md

## Project Overview

Content extraction pipeline. Extracts YouTube transcripts, web articles, PDF papers (especially arxiv), local files (.md, .txt, .docx, .doc, .html, .mhtml, .pptx), podcast episodes, and X/Twitter posts/threads, saving them as structured Markdown with sections. Supports member-only YouTube content, Whisper audio fallback, PDF layout analysis via opendataloader-pdf, arXiv API metadata, local file format detection, MHTML web archive decoding via stdlib email module, PowerPoint slide/notes extraction via python-pptx, podcast RSS feed parsing, Nitter-based tweet extraction, note tweet (long tweet) full-text recovery, DraftJS-based X Article extraction, Azure OpenAI vision-powered image description, MarkItDown-based file conversion, and LLM-powered auto-polish for speech-to-text content.

## Key Files

- `content_extractor.py` — CLI entry point (thin wrapper, delegates to package)
- `content_extractor/` — Main package (Python 3.10+, auto-installs dependencies)
- `content/config.yaml` — Configuration file (single source of truth for all defaults)
- `.claude/skills/content-extractor/SKILL.md` — Claude skill definition
- `.claude/commands/content-extractor.md` — `/content-extractor` slash command

## How to Run

```bash
# YouTube video
python3 content_extractor.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Web article
python3 content_extractor.py "https://example.com/article"

# Member-only content
python3 content_extractor.py --cookies-from-browser chrome "URL"

# PDF paper (arxiv or direct URL)
python3 content_extractor.py "https://arxiv.org/abs/2301.07041"
python3 content_extractor.py "https://example.com/paper.pdf"

# Local PDF file
python3 content_extractor.py ./paper.pdf

# Local files (.md, .txt, .docx, .doc, .html, .mhtml, .pptx)
python3 content_extractor.py ./document.md
python3 content_extractor.py ./report.docx
python3 content_extractor.py ./notes.txt
python3 content_extractor.py ./page.html
python3 content_extractor.py ./saved-page.mhtml
python3 content_extractor.py ./slides.pptx
python3 content_extractor.py --no-speaker-notes ./slides.pptx

# Disable image description (skip Azure OpenAI vision for embedded images)
python3 content_extractor.py --no-images ./report.docx
python3 content_extractor.py --no-images "https://example.com/article"

# Podcast (RSS feed or platform URL)
python3 content_extractor.py "https://feeds.example.com/podcast.xml"
python3 content_extractor.py "https://podcasts.apple.com/us/podcast/episode/id123"
python3 content_extractor.py --max-episodes 3 "https://feeds.example.com/podcast.rss"

# X/Twitter post (no auth needed for regular tweets)
python3 content_extractor.py "https://x.com/user/status/123456789"

# X Article (full content requires cookies.txt — see below)
python3 content_extractor.py --cookies cookies.txt "https://x.com/user/status/123456789"

# Tweet with custom Nitter instance (last resort, supports threads)
python3 content_extractor.py --nitter-instance nitter.net "https://twitter.com/user/status/123"

# Playlist or batch (mixed YouTube + articles + PDFs + local files + podcasts + tweets)
python3 content_extractor.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" "https://example.com/article" ./report.docx
python3 content_extractor.py -f urls.txt

# Preview without downloading
python3 content_extractor.py --dry-run "URL" ./file.docx
```

## Output

- Directory: `./content/`
- Filename: `YYYY-MM-DD_title-slug.md`
- Format: YAML frontmatter + `##` section headers + paragraph text
- YouTube outputs use `transcript.md`, articles use `article.md`, PDFs use `paper.md`, local files use `document.md`, PowerPoint uses `presentation.md`, podcasts use `podcast.md`, tweets use `tweet.md`

## Dependencies

- Python 3.10+
- PyYAML (auto-installed on first run if missing)
- yt-dlp (auto-installed on first YouTube run if missing)
- requests (auto-installed on first article fetch if missing)
- trafilatura (auto-installed on first article extraction if missing)
- faster-whisper (auto-installed on first Whisper fallback if missing)
- opendataloader-pdf (auto-installed on first PDF extraction if missing; requires Java 11+)
- python-docx (auto-installed on first .docx extraction if missing)
- python-pptx (auto-installed on first .pptx extraction if missing)
- mammoth (auto-installed on first .doc extraction if missing)
- feedparser (auto-installed on first podcast RSS feed parsing if missing)
- beautifulsoup4 (auto-installed on first tweet extraction if missing)
- playwright (auto-installed on first X Article extraction; downloads Chromium ~170MB)
- openai (auto-installed for Azure OpenAI backend)
- python-dotenv (auto-installed for .env credential loading)
- markitdown[all] (auto-installed for MarkItDown file conversion)

## Configuration

All settings are managed via `./content/config.yaml` — the single source of truth.

**Precedence**: CLI flags > config.yaml > builtin defaults

**Config sections**: `output`, `subtitles`, `auth`, `network`, `whisper`, `llm`, `vision`, `text`, `flags`, `articles`, `pdf`, `local_files`, `podcast`, `twitter`, `markitdown`, `urls`

On first run, a fully-commented `config.yaml` template is generated with all defaults. If a legacy `yt_transcripts/config.yaml` exists, it is auto-migrated to `content/config.yaml`.

The `Config` dataclass tree in `config.py` provides typed access throughout the pipeline. CLI uses `load_config()` → `apply_cli_overrides()` to build the final `Config` object.

## Architecture

Modular package (`content_extractor/`) with these modules:

| Module | Responsibility |
|--------|---------------|
| `models.py` | Data classes: `SubtitleCue`, `Chapter`, `VideoInfo`, `TranscriptResult`, `ArticleSection`, `ArticleInfo`, `ArticleResult`, `PodcastEpisodeInfo`, `PodcastResult`, `TweetInfo` (includes `tweet_subtype`: "tweet"/"note_tweet"/"x_article"), `TweetResult` |
| `exceptions.py` | Error hierarchy: `PipelineError` + 14 subclasses (incl. `MarkItDownError`) |
| `config.py` | Config dataclasses, YAML loading, CLI override merging, migration |
| `deps.py` | Auto-install yt-dlp, PyYAML, requests, trafilatura, python-docx, python-pptx, mammoth, feedparser, beautifulsoup4, playwright, browser-cookie3, openai, python-dotenv, markitdown, opendataloader-pdf if missing |
| `retry.py` | Shared retry-with-backoff utility (used by ytdlp and http_fetch) |
| `url_detect.py` | URL/file classification (YouTube, PDF, podcast, tweet, local file, article) |
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
| `llm.py` | LLM-based polish via Azure OpenAI (parallel chunked processing) |
| `llm_backend.py` | Unified Azure OpenAI backend — chat and vision completions with rate limiting |
| `rate_limiter.py` | Proactive rate limiter for Azure OpenAI (sliding-window TPM/RPM tracking) |
| `markitdown_bridge.py` | MarkItDown integration — file-to-Markdown converter with lazy singleton |
| `pipeline.py` | Single-video orchestration, dry-run |
| `arxiv.py` | ArXiv URL resolution, Atom API metadata fetch |
| `pdf.py` | PDF text extraction via opendataloader-pdf, heading detection, section assembly |
| `pdf_pipeline.py` | Single-PDF orchestration, dry-run |
| `local_file.py` | Local file extraction (.md, .txt, .docx, .doc, .html, .mhtml, .pptx) |
| `local_file_pipeline.py` | Single-local-file orchestration, dry-run |
| `podcast.py` | Podcast RSS feed parsing, episode metadata extraction |
| `podcast_pipeline.py` | Single-podcast-episode orchestration, feed resolution, dry-run |
| `tweet.py` | Twitter/X extraction via syndication API (primary), oEmbed (fallback), Playwright+cookies (X Articles), Nitter (last resort); URL normalization, t.co expansion, link-only extraction, note tweet full-text recovery (API nested keys + Playwright fallback), DraftJS block parsing for X Articles, scroll-to-bottom for lazy-loaded content |
| `tweet_pipeline.py` | Single-tweet orchestration, dry-run |
| `vision.py` | Image description via Azure OpenAI vision — parallel batch processing, marker replacement |
| `cli.py` | Argument parsing + `main()` batch loop with URL/file dispatch |

### YouTube Pipeline stages:
1. **yt-dlp metadata** (`ytdlp.py`) — fetch video info, chapters, available subtitle languages
2. **Language selection** (`subtitles.py`) — auto-detect from video metadata; prefer manual subs over auto-generated
3. **Subtitle download** (`subtitles.py`) — VTT format to temp directory; if no subs, falls back to Whisper audio transcription (`whisper.py`)
4. **VTT parsing** (`subtitles.py`) — strip timestamps, HTML tags, word-level timing markers
5. **Deduplication** (`subtitles.py`) — remove rolling-window overlaps in auto-generated subs
6. **Chapter alignment** (`text.py`) — single-pass O(n) merge of cues to chapter boundaries
7. **Text assembly** (`text.py`) — CJK-aware paragraph formation (no-space joining for Chinese/Japanese)
8. **Image description** (`vision.py`, optional) — extract images, describe via Azure OpenAI vision, insert text descriptions inline
9. **Markdown generation** (`markdown.py`) — YAML frontmatter, metadata blockquote, chapter sections
10. **Auto-polish** (`llm.py`, speech-to-text only) — Azure OpenAI fixes punctuation, speech-recognition errors, CJK formatting

### Article Pipeline stages:
1. **HTTP fetch** (`http_fetch.py`) — download HTML with retry, UA rotation, SSL handling
2. **Content extraction** (`article.py`) — trafilatura XML extraction preserving headings
3. **Metadata extraction** (`article.py`) — title, author, date, site name, language
4. **Section assembly** (`article.py`) — headings → `ArticleSection` objects
5. **Image description** (`vision.py`, optional) — extract images, describe via Azure OpenAI vision, insert text descriptions inline
6. **Markdown generation** (`markdown.py`) — YAML frontmatter, metadata, section body
7. **Auto-polish** (`llm.py`, speech-to-text only) — same as YouTube

### PDF Pipeline stages:
1. **URL classification** (`url_detect.py`) — detect arxiv or `.pdf` URLs, local PDF files
2. **ArXiv resolution** (`arxiv.py`, optional) — extract ID, fetch Atom API metadata (title, authors, abstract, categories)
3. **PDF download** (`http_fetch.py`) — binary fetch with retry/backoff, or read local file
4. **Layout-aware extraction** (`pdf.py`) — opendataloader-pdf for two-column handling, tables, headings
5. **Section assembly** (`pdf.py`) — parse Markdown output into `ArticleSection` objects
6. **Abstract extraction** (`pdf.py`) — pull abstract out for frontmatter
7. **Math detection** (`pdf.py`) — flag presence of mathematical notation
8. **Image description** (`vision.py`, optional) — extract images, describe via Azure OpenAI vision, insert text descriptions inline
9. **Markdown generation** (`markdown.py`) — YAML frontmatter with arxiv metadata, section body

### Local File Pipeline stages:
1. **File detection** (`url_detect.py`) — classify by extension (.md, .txt, .docx, .doc, .html, .htm, .mhtml, .mht, .pptx, .ppt)
2. **Format dispatch** (`local_file.py`) — route to per-format extractor
3. **Content extraction** (`local_file.py`) — .md: YAML frontmatter + heading parsing; .txt: paragraph splitting + pseudo-heading detection; .docx: python-docx style extraction; .doc: mammoth → HTML → trafilatura; .html: trafilatura; .mhtml/.mht: MIME decode via stdlib email module → trafilatura; .pptx: python-pptx slide text, tables, speaker notes extraction (each slide → one section); .ppt: not supported (clear error directing to convert to .pptx)
4. **Image description** (`vision.py`, optional) — extract images, describe via Azure OpenAI vision, insert text descriptions inline
5. **Markdown generation** (`markdown.py`) — YAML frontmatter with file metadata, section body (reuses `build_article_markdown()`)

### Podcast Pipeline stages:
1. **URL classification** (`url_detect.py`) — detect podcast platform URLs or RSS feed URLs
2. **Feed resolution** (`podcast_pipeline.py`) — expand RSS feed to individual episode URLs via feedparser; for platform URLs, fallback to yt-dlp
3. **Metadata extraction** (`podcast.py`) — episode title, show name, episode number, date, duration, description from RSS or yt-dlp JSON
4. **Audio download** (`whisper.py`) — reuses existing yt-dlp audio download pipeline
5. **Transcription** (`whisper.py`) — reuses existing Whisper transcription, returns SubtitleCue list
6. **Text assembly** (`text.py`) — reuses CJK-aware paragraph formation from YouTube pipeline
7. **Image description** (`vision.py`, optional) — extract images, describe via Azure OpenAI vision, insert text descriptions inline
8. **Markdown generation** (`markdown.py`) — YAML frontmatter with podcast metadata, Whisper warning, transcript body

### Twitter/X Pipeline stages:
1. **URL normalization** (`tweet.py`) — normalize twitter.com/x.com/nitter URLs to canonical form, extract tweet ID
2. **Cascade fetch** (`tweet.py`) — try syndication API (primary, no auth) → oEmbed API (fallback) → Nitter (last resort). Syndication/oEmbed return single tweets only; thread support requires a working Nitter instance.
3. **Note tweet detection** (`tweet.py`) — syndication API truncates long tweets (note_tweets by premium users). Full text is recovered via nested API keys (`note_tweet`/`noteTweet` → `note_tweet_results`/`noteTweetResults` → `result.text`). If the API only returns an ID without text, Playwright renders the tweet page and scrapes `[data-testid="tweetText"]` as a fallback. Sets `tweet_subtype="note_tweet"`.
4. **X Article extraction** (`tweet.py`) — if syndication detects an X Article and `--cookies` is provided, Playwright launches headless Chromium with injected cookies, scrolls to bottom to trigger lazy-loaded content, then tries DraftJS block parsing first (parses `[data-testid="longformRichTextComponent"]` blocks with heading/list/paragraph classification). Falls back to trafilatura if DraftJS extraction fails. Without cookies, returns preview-only. Sets `tweet_subtype="x_article"`.
5. **Link-only extraction** (`tweet.py`, `article.py`) — if tweet text is only URL(s) pointing to external sites, fetches and extracts article content via trafilatura
6. **t.co link expansion** (`tweet.py`) — best-effort HEAD requests to expand shortened URLs (syndication/oEmbed paths only; Nitter expands links in its HTML)
7. **Content completeness check** (`tweet.py`) — warns if extracted text appears truncated (short text ending with ellipsis or URL)
8. **Section assembly** (`tweet.py`) — tweet text becomes an `ArticleSection`; Nitter path supports multiple sections for threads; DraftJS path produces structured sections with headings and ordered/unordered lists
9. **Image description** (`vision.py`, optional) — extract images, describe via Azure OpenAI vision, insert text descriptions inline
10. **Body assembly** (`article.py`) — reuses `sections_to_body_text()` from article pipeline
11. **Markdown generation** (`markdown.py`) — YAML frontmatter with tweet metadata, thread indicator, `tweet_subtype` (omitted when "tweet", included for "note_tweet" or "x_article")

#### Exporting cookies for X Articles

X Articles (`x.com/i/article/...`) are JS-rendered SPAs that require an authenticated session. Chrome 127+ uses app-bound cookie encryption, so `--cookies-from-browser chrome` cannot decrypt them. Instead, export a cookies.txt file:

1. Open Chrome → go to `x.com` (logged in)
2. Install the **"Get cookies.txt LOCALLY"** extension ([Chrome Web Store](https://chromewebstore.google.com/))
3. Click the extension on any `x.com` page → export as `cookies.txt`
4. Place the file in the project directory (or specify full path)
5. Run: `python3 content_extractor.py --cookies cookies.txt "https://x.com/user/status/..."`

Cookies last ~1 year. Re-export only if you log out, change password, or get auth errors. Without `--cookies`, X Articles output a preview (title + short excerpt from the syndication API).

## Conventions

- URLs are auto-classified: YouTube domains → video pipeline, arxiv/`.pdf` URLs → PDF pipeline, `twitter.com`/`x.com`/`nitter.*` with `/status/` → tweet pipeline, podcast platform URLs and RSS feeds → podcast pipeline, everything else → article pipeline. Local files are detected by extension: `.pdf` → PDF pipeline, `.md`/`.txt`/`.docx`/`.doc`/`.html`/`.htm`/`.mhtml`/`.mht`/`.pptx`/`.ppt` → local file pipeline.
- Errors are classified: `VideoUnavailableError`, `AuthRequiredError`, `NoSubtitlesError`, `NetworkError`, `WhisperError`, `LLMError`, `ArticleFetchError`, `ContentExtractionError`, `PDFExtractionError`, `ArxivAPIError`, `LocalFileError`, `PodcastFetchError`, `TweetFetchError`, `MarkItDownError`
- Network errors retry with exponential backoff via shared `retry.py` utility
- Batch processing: per-item errors are caught and logged, don't stop the batch
- All defaults stored in `./content/config.yaml` (CLI flags override)
- Filename collisions resolved by appending `-2`, `-3`, etc.
- `--no-images` disables image extraction and Azure OpenAI vision description across all pipelines

## Auto-Polish

Speech-to-text outputs (YouTube transcripts, podcast episodes) are automatically polished via Azure OpenAI:
- Fix punctuation, capitalization, speech-recognition artifacts
- Preserve original language (no translation)
- CJK: fix segmentation and punctuation placement
- Deployment override: `--polish-model MODEL` or set `llm.polish_model` in config.yaml
- Raw content saved as `.unpolished.md`, polished version as `.md`
