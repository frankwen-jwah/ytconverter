"""Twitter/X extraction — fetch tweets via syndication API, oEmbed, or Nitter."""

import re
from typing import List, Optional, Tuple, TYPE_CHECKING

from .exceptions import TweetFetchError
from .http_fetch import fetch_html_simple
from .models import ArticleSection, ExtractedImage, TweetInfo

if TYPE_CHECKING:
    from .config import NetworkConfig, TwitterConfig


def _normalize_tweet_url(url: str) -> str:
    """Normalise a tweet URL to canonical ``https://x.com/{user}/status/{id}``."""
    url = url.strip()
    # Replace nitter domains with x.com
    url = re.sub(r"https?://nitter\.[^/]+/", "https://x.com/", url)
    # Replace twitter.com with x.com
    url = re.sub(r"https?://(\w+\.)*twitter\.com/", "https://x.com/", url)
    url = re.sub(r"https?://www\.x\.com/", "https://x.com/", url)
    # Ensure https
    if url.startswith("http://x.com"):
        url = "https" + url[4:]
    # Strip tracking params
    url = re.sub(r"[?&](t|s|ref_src|ref_url|src)=[^&]*", "", url)
    url = url.rstrip("?&")
    return url


def _build_nitter_url(tweet_url: str, nitter_instance: str) -> str:
    """Map an x.com tweet URL to a Nitter instance URL."""
    # Extract path from x.com URL
    match = re.match(r"https?://x\.com(/.*)", tweet_url)
    if not match:
        raise TweetFetchError(f"Cannot parse tweet URL: {tweet_url}")
    path = match.group(1)
    host = nitter_instance.rstrip("/")
    if not host.startswith("http"):
        host = f"https://{host}"
    return f"{host}{path}"


def _parse_nitter_html(html: str, original_url: str) -> Tuple[TweetInfo, List[ArticleSection]]:
    """Parse Nitter HTML to extract tweet/thread content.

    Returns ``(TweetInfo, list_of_sections)``.
    """
    from .deps import ensure_beautifulsoup
    ensure_beautifulsoup()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # --- Author info ---
    author_handle = ""
    author_name = ""
    header = soup.select_one(".main-tweet .tweet-header, .main-tweet .fullname-and-username")
    if header:
        fullname_el = header.select_one(".fullname")
        if fullname_el:
            author_name = fullname_el.get_text(strip=True)
        username_el = header.select_one(".username")
        if username_el:
            author_handle = username_el.get_text(strip=True)

    # Fallback: try extracting from the page title or tweet-avatar links
    if not author_handle:
        avatar_link = soup.select_one(".tweet-avatar a, a.username")
        if avatar_link:
            href = avatar_link.get("href", "")
            author_handle = f"@{href.strip('/')}" if href else ""
        # Try from page title: "username: tweet text - Nitter"
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text()
            m = re.match(r"(@?\w+):", title_text)
            if m:
                handle = m.group(1)
                author_handle = handle if handle.startswith("@") else f"@{handle}"

    if not author_name:
        author_name = author_handle.lstrip("@")

    # --- Date ---
    publish_date = "unknown"
    date_el = soup.select_one(".main-tweet .tweet-date a, .main-tweet time")
    if date_el:
        title_attr = date_el.get("title", "")
        # Nitter date format: "Mar 15, 2025 · 10:30 AM UTC"
        date_match = re.search(r"(\w+ \d+, \d{4})", title_attr)
        if date_match:
            try:
                from datetime import datetime
                dt = datetime.strptime(date_match.group(1), "%b %d, %Y")
                publish_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                publish_date = date_match.group(1)
        elif title_attr:
            publish_date = title_attr.strip()

    # --- Tweet content ---
    sections: List[ArticleSection] = []

    # Collect main thread tweets
    main_thread = soup.select(".main-thread .timeline-item .tweet-content, "
                              ".main-tweet .tweet-content")
    if not main_thread:
        # Fallback: try any tweet-content on the page
        main_thread = soup.select(".tweet-content")

    if not main_thread:
        raise TweetFetchError(
            f"Could not extract tweet content from Nitter page for {original_url}"
        )

    for i, content_el in enumerate(main_thread):
        text = content_el.get_text(separator="\n", strip=True)
        if not text:
            continue
        heading = ""
        level = 2
        if len(main_thread) > 1:
            heading = f"{author_handle} ({i + 1}/{len(main_thread)})"
        sections.append(ArticleSection(heading=heading, level=level, body=text))

    # Calculate stats
    full_text = "\n\n".join(s.body for s in sections)
    word_count = len(full_text.split())
    is_thread = len(sections) > 1
    title = _make_title(full_text)

    info = TweetInfo(
        title=title,
        url=original_url,
        author=author_handle,
        author_name=author_name,
        publish_date=publish_date,
        word_count=word_count,
        is_thread=is_thread,
        thread_length=len(sections),
    )
    return info, sections


def fetch_tweet_via_nitter(
    url: str,
    twitter_config: "TwitterConfig",
    network_config: "NetworkConfig",
) -> Tuple[TweetInfo, List[ArticleSection]]:
    """Fetch and parse a tweet/thread via Nitter instances.

    Tries the primary instance, then each fallback. Raises ``TweetFetchError``
    if all instances fail.
    """
    canonical = _normalize_tweet_url(url)

    instances = [twitter_config.nitter_instance] + list(
        twitter_config.nitter_fallback_instances or []
    )
    last_error: Optional[Exception] = None

    for instance in instances:
        nitter_url = _build_nitter_url(canonical, instance)
        try:
            html = fetch_html_simple(
                nitter_url,
                timeout=twitter_config.timeout,
                network_retries=network_config.retries,
                backoff_base=network_config.backoff_base,
                rotate_ua=twitter_config.user_agent_rotation,
                verify_ssl=twitter_config.verify_ssl,
                error_class=TweetFetchError,
            )
            return _parse_nitter_html(html, canonical)
        except TweetFetchError as exc:
            last_error = exc
            print(f"  [tweet] Nitter instance {instance} failed: {exc}", flush=True)
            continue
        except Exception as exc:
            last_error = exc
            print(f"  [tweet] Nitter instance {instance} error: {exc}", flush=True)
            continue

    raise TweetFetchError(
        f"All Nitter instances failed for {canonical}: {last_error}"
    )


# ---------------------------------------------------------------------------
# Tombstone sentinel — definitively deleted, don't cascade to other providers
# ---------------------------------------------------------------------------


class _TweetTombstoneError(TweetFetchError):
    """Tweet is definitively deleted/tombstoned — do not cascade."""


# ---------------------------------------------------------------------------
# Syndication API
# ---------------------------------------------------------------------------

_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"


def _extract_tweet_id(url: str) -> str:
    """Extract numeric tweet ID from a canonical x.com URL."""
    m = re.search(r"/status/(\d+)", url)
    if not m:
        raise TweetFetchError(f"Cannot extract tweet ID from URL: {url}")
    return m.group(1)


def _expand_tco_urls(text: str, timeout: int = 5, verify_ssl: bool = True) -> str:
    """Best-effort expansion of t.co short links to their real URLs."""
    from .deps import ensure_requests
    ensure_requests()
    import requests as _requests

    tco_links = list(set(re.findall(r"https?://t\.co/[A-Za-z0-9]+", text)))
    if not tco_links:
        return text

    for short_url in tco_links:
        try:
            resp = _requests.head(
                short_url, allow_redirects=False,
                timeout=timeout, verify=verify_ssl,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            location = resp.headers.get("Location", "")
            if not location:
                continue
            # Follow one more hop if still a t.co redirect
            if "t.co/" in location:
                resp2 = _requests.head(
                    location, allow_redirects=False,
                    timeout=timeout, verify=verify_ssl,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                location = resp2.headers.get("Location", location)
            if location and location != short_url:
                text = text.replace(short_url, location)
        except Exception:
            continue  # leave original t.co link in place
    return text


def _is_link_only(text: str) -> bool:
    """Return True if text contains only URL(s) and no meaningful content."""
    stripped = re.sub(r"https?://\S+", "", text).strip()
    return len(stripped) == 0


def _extract_urls(text: str) -> List[str]:
    """Extract all URLs from text."""
    return re.findall(r"https?://\S+", text)


def _extract_linked_content(
    url: str, twitter_config: "TwitterConfig",
) -> Tuple[str, List[ArticleSection]]:
    """Fetch a linked page and extract article content.

    Returns ``(title, sections)``. Raises on failure.
    """
    from .deps import ensure_requests, ensure_trafilatura
    ensure_requests()
    ensure_trafilatura()
    from .article import extract_article
    from .config import ArticlesConfig

    html = fetch_html_simple(
        url,
        timeout=twitter_config.timeout,
        verify_ssl=twitter_config.verify_ssl,
        rotate_ua=twitter_config.user_agent_rotation,
        error_class=TweetFetchError,
    )
    articles_config = ArticlesConfig()
    info, sections, _imgs = extract_article(html, url, articles_config)
    return info.title or "", sections


def _parse_cookies_txt(cookies_path: str, domain: str = ".x.com") -> List[dict]:
    """Parse a Netscape cookies.txt file, filtering by domain."""
    pw_cookies = []
    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # #HttpOnly_ prefix marks HttpOnly cookies, not comments
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                cookie_domain = parts[0]
                if domain not in cookie_domain:
                    continue
                pw_cookies.append({
                    "name": parts[5],
                    "value": parts[6],
                    "domain": cookie_domain,
                    "path": parts[2],
                    "secure": parts[3].upper() == "TRUE",
                })
    except Exception as exc:
        raise TweetFetchError(f"Cannot read cookies file: {exc}") from exc
    return pw_cookies


def _scroll_to_bottom(page, max_scrolls: int = 30, scroll_pause_ms: int = 800,
                      stable_threshold: int = 3) -> None:
    """Scroll a Playwright page to the bottom to trigger lazy-loaded content.

    X Articles are JS-rendered SPAs that load content as the user scrolls.
    Scrolls by viewport-height increments and monitors document height.
    Stops when height is stable for *stable_threshold* consecutive checks.
    """
    prev_height = 0
    stable_count = 0

    for _ in range(max_scrolls):
        current_height = page.evaluate("document.body.scrollHeight")

        if current_height == prev_height:
            stable_count += 1
            if stable_count >= stable_threshold:
                break
        else:
            stable_count = 0
            prev_height = current_height

        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(scroll_pause_ms)

    # Scroll back to top (trafilatura works on full DOM regardless, but
    # some extractors behave better with the page at the start position)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)


def _fetch_note_tweet_via_playwright(
    tweet_url: str,
    timeout: int = 30,
    cookies_path: Optional[str] = None,
) -> Optional[str]:
    """Fetch full text of a note_tweet (long tweet) via headless browser.

    The syndication and oEmbed APIs truncate note_tweet text.  This function
    renders the tweet page and extracts the full text from the DOM.
    No authentication is required for public tweets, but cookies are used
    if available to improve reliability.
    """
    from .deps import ensure_playwright
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    pw_cookies: list = []
    if cookies_path:
        try:
            pw_cookies = _parse_cookies_txt(cookies_path, domain=".x.com")
        except Exception:
            pass  # proceed without cookies

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0.0.0 Safari/537.36",
            )
            if pw_cookies:
                context.add_cookies(pw_cookies)
            page = context.new_page()
            page.goto(tweet_url, wait_until="domcontentloaded",
                      timeout=timeout * 1000)

            # Wait for tweet text to render
            try:
                page.wait_for_selector(
                    '[data-testid="tweetText"]', timeout=15000)
            except Exception:
                page.wait_for_timeout(5000)

            # Extract text from the main tweet element (first match)
            elements = page.query_selector_all('[data-testid="tweetText"]')
            if elements:
                full_text = elements[0].inner_text()
                return full_text.strip() if full_text else None
        finally:
            browser.close()

    return None


def _parse_draftjs_blocks(page) -> Tuple[Optional[str], List[ArticleSection]]:
    """Parse DraftJS blocks from an X Article's longformRichTextComponent.

    X Articles use a DraftJS editor that renders content as blocks with
    classes like ``longform-header-two``, ``longform-unstyled``,
    ``longform-ordered-list-item``, ``longform-blockquote``, etc.
    Returns ``(title, sections)`` extracted from the DOM.
    """
    blocks = page.evaluate('''() => {
        const el = document.querySelector(
            '[data-testid="longformRichTextComponent"]');
        if (!el) return null;
        const nodes = el.querySelectorAll('[data-block="true"]');
        return [...nodes].map(b => ({
            cls: b.className.split(" ")[0] || "",
            tag: b.tagName,
            text: b.innerText.trim()
        }));
    }''')
    if not blocks:
        return None, []

    # Map DraftJS classes to heading levels
    _HEADING_MAP = {
        "longform-header-one": 1,
        "longform-header-two": 2,
        "longform-header-three": 3,
    }

    title: Optional[str] = None
    sections: List[ArticleSection] = []
    current_heading = ""
    current_level = 2
    current_paragraphs: List[str] = []
    ordered_counter = 0

    def _flush():
        nonlocal current_heading, current_paragraphs, ordered_counter
        if current_heading or current_paragraphs:
            sections.append(ArticleSection(
                heading=current_heading,
                level=current_level,
                body="\n\n".join(current_paragraphs),
            ))
        current_heading = ""
        current_paragraphs = []
        ordered_counter = 0

    for block in blocks:
        cls = block["cls"]
        text = block["text"]
        if not text:
            continue

        level = _HEADING_MAP.get(cls)
        if level is not None:
            _flush()
            if title is None:
                title = text
            current_heading = text
            current_level = level
        elif cls == "longform-ordered-list-item":
            ordered_counter += 1
            current_paragraphs.append(f"{ordered_counter}. {text}")
        elif cls == "longform-unordered-list-item":
            ordered_counter = 0
            current_paragraphs.append(f"\u2022 {text}")
        else:
            # unstyled, blockquote, etc. — reset ordered counter
            ordered_counter = 0
            current_paragraphs.append(text)

    _flush()
    return title, sections


def _fetch_x_article_via_playwright(
    article_url: str,
    cookies_path: str,
    timeout: int = 30,
) -> Tuple[Optional[str], List[ArticleSection]]:
    """Fetch an X Article's full content via headless browser with cookies.

    Uses a Netscape cookies.txt file for authentication.
    Returns ``(title, sections)`` or raises on failure.
    """
    from .deps import ensure_playwright
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    pw_cookies = _parse_cookies_txt(cookies_path, domain=".x.com")
    if not pw_cookies:
        raise TweetFetchError(
            f"No x.com cookies found in {cookies_path}. "
            "Make sure the file contains cookies from x.com.")

    print(f"  [tweet] Loaded {len(pw_cookies)} x.com cookies from "
          f"{cookies_path}", flush=True)

    # Launch headless browser and fetch article
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0.0.0 Safari/537.36",
            )
            context.add_cookies(pw_cookies)
            page = context.new_page()
            page.goto(article_url, wait_until="domcontentloaded",
                      timeout=timeout * 1000)

            # X Articles use DraftJS — wait for the longform content
            # container rather than a semantic <article> tag.
            try:
                page.wait_for_selector(
                    '[data-testid="longformRichTextComponent"]',
                    timeout=15000)
            except Exception:
                # Fall back to <article> for older/different layouts
                try:
                    page.wait_for_selector("article", timeout=5000)
                except Exception:
                    page.wait_for_timeout(5000)

            # Scroll to bottom to trigger lazy-loaded content
            print("  [tweet] Scrolling X Article to load all content...",
                  flush=True)
            _scroll_to_bottom(page)

            # Try DraftJS block parsing first (preferred for X Articles)
            dj_title, dj_sections = _parse_draftjs_blocks(page)
            if dj_sections:
                body_text = "\n\n".join(s.body for s in dj_sections)
                article_words = len(body_text.split())
                print(f"  [tweet] Extracted X Article via DraftJS: "
                      f"{article_words} words, {len(dj_sections)} sections",
                      flush=True)
                return dj_title or "", dj_sections

            # Fallback: capture full HTML and parse with trafilatura
            html = page.content()
        finally:
            browser.close()

    from .deps import ensure_trafilatura
    ensure_trafilatura()
    from .article import extract_article
    from .config import ArticlesConfig

    articles_config = ArticlesConfig()
    info, sections, _imgs = extract_article(html, article_url, articles_config)

    # Warn if extracted content seems suspiciously short for an X Article
    body_text = "\n\n".join(s.body for s in sections)
    article_words = len(body_text.split())
    if article_words < 100:
        print(f"  [tweet] WARNING: X Article extraction yielded only "
              f"{article_words} words — content may be incomplete.",
              flush=True)

    return info.title or "", sections


def _make_title(text: str, max_len: int = 80) -> str:
    """Build a display title from tweet text (first ~max_len chars)."""
    clean = text[:max_len].replace("\n", " ")
    if len(text) > max_len:
        clean = clean.rsplit(" ", 1)[0] + "..."
    return clean


def _fetch_syndication_json(tweet_id: str, timeout: int,
                            verify_ssl: bool) -> dict:
    """Fetch tweet JSON from Twitter's syndication API."""
    from .deps import ensure_requests
    ensure_requests()
    import requests as _requests

    try:
        resp = _requests.get(
            _SYNDICATION_URL,
            params={"id": tweet_id, "token": "x"},
            timeout=timeout, verify=verify_ssl,
            headers={"Accept": "application/json",
                     "User-Agent": "Mozilla/5.0"},
        )
    except Exception as exc:
        raise TweetFetchError(f"Syndication API request failed: {exc}") from exc

    if resp.status_code == 404:
        raise TweetFetchError("Tweet not found (syndication 404)")
    if resp.status_code != 200:
        raise TweetFetchError(
            f"Syndication API HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise TweetFetchError(
            f"Invalid JSON from syndication API: {exc}") from exc

    if not data:
        raise TweetFetchError("Syndication API returned empty response")

    # Tombstoned / deleted tweets — short-circuit, don't cascade
    if data.get("__typename") == "TweetTombstone":
        tombstone_text = ""
        tombstone = data.get("tombstone", {})
        if isinstance(tombstone, dict):
            text_obj = tombstone.get("text", {})
            if isinstance(text_obj, dict):
                tombstone_text = text_obj.get("text", "")
        raise _TweetTombstoneError(
            f"Tweet deleted or unavailable: {tombstone_text or 'tombstoned'}")

    return data


def _extract_note_tweet_text(data: dict) -> Optional[str]:
    """Extract full text from a note_tweet (long tweet by premium users).

    The syndication API returns truncated text in ``data["text"]`` for long
    tweets.  The full text lives in nested structures whose key casing varies
    across API versions.  Returns ``None`` when the tweet is not a note_tweet.
    """
    for outer_key in ("note_tweet", "noteTweet"):
        note = data.get(outer_key)
        if not isinstance(note, dict):
            continue
        for inner_key in ("note_tweet_results", "noteTweetResults"):
            results = note.get(inner_key)
            if not isinstance(results, dict):
                continue
            result = results.get("result")
            if isinstance(result, dict):
                full_text = result.get("text", "")
                if full_text and len(full_text) > len(data.get("text", "")):
                    return full_text
    return None


def _parse_syndication_response(
    data: dict,
    canonical_url: str,
    twitter_config: "TwitterConfig",
    auth_config: "Optional[object]" = None,
) -> Tuple[TweetInfo, List[ArticleSection]]:
    """Transform syndication JSON into (TweetInfo, sections)."""
    user = data.get("user", {})
    screen_name = user.get("screen_name", "")
    author_handle = f"@{screen_name}" if screen_name else ""
    author_name = user.get("name", screen_name)

    # Date: ISO 8601 → YYYY-MM-DD
    publish_date = "unknown"
    created_at = data.get("created_at", "")
    if created_at:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            publish_date = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    # Text: prefer note_tweet full text over truncated data["text"]
    text = data.get("text", "")
    is_note_tweet = False
    note_text = _extract_note_tweet_text(data)
    if note_text:
        print(f"  [tweet] Note tweet detected — using full text "
              f"({len(note_text)} chars vs {len(text)} truncated)",
              flush=True)
        text = note_text
        is_note_tweet = True

    # Note tweets: syndication API only returns an ID, not full text.
    # Fall back to Playwright to render the tweet page and scrape it.
    note_tweet_data = data.get("note_tweet") or data.get("noteTweet")
    if (not is_note_tweet
            and isinstance(note_tweet_data, dict)
            and note_tweet_data.get("id")):
        try:
            print("  [tweet] Note tweet detected — fetching full text "
                  "via browser...", flush=True)
            cookies_path = (getattr(auth_config, "cookies", None)
                            if auth_config else None)
            if not cookies_path:
                from .config import DEFAULT_COOKIES_FILE
                if DEFAULT_COOKIES_FILE.exists():
                    cookies_path = str(DEFAULT_COOKIES_FILE)
            pw_text = _fetch_note_tweet_via_playwright(
                canonical_url,
                timeout=twitter_config.timeout,
                cookies_path=cookies_path,
            )
            if pw_text and len(pw_text) > len(text):
                print(f"  [tweet] Full note tweet: {len(pw_text)} chars "
                      f"(vs {len(text)} from API)", flush=True)
                text = pw_text
                is_note_tweet = True
        except Exception as exc:
            print(f"  [tweet] Browser extraction of note tweet failed: "
                  f"{exc}", flush=True)

    if text and getattr(twitter_config, "expand_tco_links", True):
        text = _expand_tco_urls(
            text,
            timeout=5,
            verify_ssl=getattr(twitter_config, "verify_ssl", True),
        )

    # X Articles: syndication includes article title + preview inline.
    # Full article content requires authentication (JS-rendered SPA).
    article_data = data.get("article")
    if article_data and isinstance(article_data, dict):
        article_title = article_data.get("title", "")
        article_preview = article_data.get("preview_text", "")
        article_id = article_data.get("rest_id", "")

        # Try Playwright extraction if cookies file is available
        # Auto-detect content/cookies.txt if no explicit --cookies flag
        cookies_path = getattr(auth_config, "cookies", None) if auth_config else None
        if not cookies_path:
            from .config import DEFAULT_COOKIES_FILE
            if DEFAULT_COOKIES_FILE.exists():
                cookies_path = str(DEFAULT_COOKIES_FILE)
        has_cookies = bool(cookies_path)
        if has_cookies and article_id:
            try:
                article_url = f"https://x.com/i/article/{article_id}"
                print("  [tweet] X Article — fetching full content via "
                      "browser...", flush=True)
                pw_title, pw_sections = _fetch_x_article_via_playwright(
                    article_url,
                    cookies_path,
                    timeout=twitter_config.timeout,
                )
                if pw_sections:
                    body = "\n\n".join(s.body for s in pw_sections)
                    info = TweetInfo(
                        title=_make_title(
                            article_title or pw_title or body),
                        url=canonical_url,
                        author=author_handle,
                        author_name=author_name,
                        publish_date=publish_date,
                        word_count=len(body.split()),
                        is_thread=False,
                        thread_length=1,
                        tweet_subtype="x_article",
                    )
                    return info, pw_sections
            except Exception as exc:
                print(f"  [tweet] Browser extraction failed: {exc}",
                      flush=True)

        # Fallback: preview-only
        if article_title or article_preview:
            if has_cookies:
                print("  [tweet] Falling back to X Article preview",
                      flush=True)
            else:
                print("  [tweet] X Article detected — preview only "
                      "(use --cookies cookies.txt for full content)",
                      flush=True)
            body = article_preview or ""
            sections = []
            if article_title:
                sections.append(ArticleSection(
                    heading=article_title, level=2, body=article_preview))
            else:
                sections.append(ArticleSection(
                    heading="", level=2, body=article_preview))
            if not has_cookies:
                sections.append(ArticleSection(
                    heading="Note", level=2,
                    body="This is an X Article preview. Export cookies "
                         "from Chrome and use --cookies cookies.txt "
                         "for full content."))
            info = TweetInfo(
                title=_make_title(article_title or article_preview),
                url=canonical_url,
                author=author_handle,
                author_name=author_name,
                publish_date=publish_date,
                word_count=len(body.split()),
                is_thread=False,
                thread_length=1,
                tweet_subtype="x_article",
            )
            return info, sections

    # Link-only tweets: extract content from the linked page
    if text and _is_link_only(text):
        for link_url in _extract_urls(text):
            try:
                print(f"  [tweet] Link-only tweet — extracting content from {link_url}",
                      flush=True)
                linked_title, linked_sections = _extract_linked_content(
                    link_url, twitter_config)
                if linked_sections:
                    body = "\n\n".join(s.body for s in linked_sections)
                    info = TweetInfo(
                        title=_make_title(linked_title or body),
                        url=canonical_url,
                        author=author_handle,
                        author_name=author_name,
                        publish_date=publish_date,
                        word_count=len(body.split()),
                        is_thread=False,
                        thread_length=1,
                    )
                    return info, linked_sections
            except Exception as exc:
                print(f"  [tweet] Could not extract linked content: {exc}",
                      flush=True)
                continue

    title = _make_title(text)
    word_count = len(text.split()) if text else 0

    # Content completeness warning for potentially truncated tweets
    if word_count < 50 and text:
        if (text.rstrip().endswith(("...", "\u2026"))
                or re.search(r"https?://\S+$", text.rstrip())):
            print(f"  [tweet] WARNING: Content may be truncated "
                  f"({word_count} words, ends with URL/ellipsis). "
                  f"This may be a long tweet that the syndication API "
                  f"did not fully return.", flush=True)

    info = TweetInfo(
        title=title,
        url=canonical_url,
        author=author_handle,
        author_name=author_name,
        publish_date=publish_date,
        word_count=word_count,
        is_thread=False,
        thread_length=1,
        tweet_subtype="note_tweet" if is_note_tweet else "tweet",
    )
    sections = [ArticleSection(heading="", level=2, body=text)] if text else []
    return info, sections


# ---------------------------------------------------------------------------
# oEmbed API
# ---------------------------------------------------------------------------

_OEMBED_URL = "https://publish.twitter.com/oembed"


def _fetch_oembed(canonical_url: str, timeout: int,
                  verify_ssl: bool) -> dict:
    """Fetch tweet oEmbed JSON from Twitter's publish API."""
    from .deps import ensure_requests
    ensure_requests()
    import requests as _requests

    try:
        resp = _requests.get(
            _OEMBED_URL,
            params={"url": canonical_url, "omit_script": "true"},
            timeout=timeout, verify=verify_ssl,
            headers={"Accept": "application/json",
                     "User-Agent": "Mozilla/5.0"},
        )
    except Exception as exc:
        raise TweetFetchError(f"oEmbed request failed: {exc}") from exc

    if resp.status_code != 200:
        raise TweetFetchError(f"oEmbed HTTP {resp.status_code}")

    content_type = resp.headers.get("Content-Type", "")
    if "json" not in content_type:
        raise TweetFetchError("oEmbed returned non-JSON response")

    try:
        return resp.json()
    except ValueError as exc:
        raise TweetFetchError(f"Invalid JSON from oEmbed: {exc}") from exc


def _parse_oembed_response(
    data: dict,
    canonical_url: str,
    twitter_config: "TwitterConfig",
) -> Tuple[TweetInfo, List[ArticleSection]]:
    """Transform oEmbed JSON into (TweetInfo, sections)."""
    from .deps import ensure_beautifulsoup
    ensure_beautifulsoup()
    from bs4 import BeautifulSoup

    author_name = data.get("author_name", "")
    # Extract handle from author_url: https://twitter.com/{handle}
    author_handle = ""
    author_url = data.get("author_url", "")
    handle_match = re.search(r"twitter\.com/(\w+)", author_url)
    if handle_match:
        author_handle = f"@{handle_match.group(1)}"

    # Parse tweet text from HTML blockquote
    html = data.get("html", "")
    text = ""
    publish_date = "unknown"
    if html:
        soup = BeautifulSoup(html, "html.parser")
        blockquote = soup.find("blockquote")
        if blockquote:
            # The blockquote contains: <p>tweet text</p> then
            # — Author (@handle) <a href="...">Date</a>
            p_tag = blockquote.find("p")
            if p_tag:
                text = p_tag.get_text(separator="\n", strip=True)
            # Extract date from the last <a> tag in blockquote
            links = blockquote.find_all("a")
            if links:
                date_text = links[-1].get_text(strip=True)
                # Format: "March 28, 2026"
                date_match = re.search(r"(\w+ \d+, \d{4})", date_text)
                if date_match:
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(date_match.group(1), "%B %d, %Y")
                        publish_date = dt.strftime("%Y-%m-%d")
                    except ValueError:
                        pass

    # Expand t.co links
    if text and getattr(twitter_config, "expand_tco_links", True):
        text = _expand_tco_urls(
            text,
            timeout=5,
            verify_ssl=getattr(twitter_config, "verify_ssl", True),
        )

    # Link-only tweets: extract content from the linked page
    if text and _is_link_only(text):
        for link_url in _extract_urls(text):
            try:
                print(f"  [tweet] Link-only tweet — extracting content from {link_url}",
                      flush=True)
                linked_title, linked_sections = _extract_linked_content(
                    link_url, twitter_config)
                if linked_sections:
                    body = "\n\n".join(s.body for s in linked_sections)
                    info = TweetInfo(
                        title=_make_title(linked_title or body),
                        url=canonical_url,
                        author=author_handle,
                        author_name=author_name,
                        publish_date=publish_date,
                        word_count=len(body.split()),
                        is_thread=False,
                        thread_length=1,
                    )
                    return info, linked_sections
            except Exception as exc:
                print(f"  [tweet] Could not extract linked content: {exc}",
                      flush=True)
                continue

    title = _make_title(text)
    word_count = len(text.split()) if text else 0

    info = TweetInfo(
        title=title,
        url=canonical_url,
        author=author_handle,
        author_name=author_name,
        publish_date=publish_date,
        word_count=word_count,
        is_thread=False,
        thread_length=1,
    )
    sections = [ArticleSection(heading="", level=2, body=text)] if text else []
    return info, sections


# ---------------------------------------------------------------------------
# Cascade dispatcher
# ---------------------------------------------------------------------------


def _extract_syndication_media(
    data: dict,
    extract_images: bool = False,
    verify_ssl: bool = True,
) -> List[ExtractedImage]:
    """Extract photo images from syndication JSON response.

    Downloads each photo and creates ``ExtractedImage`` objects with markers.
    """
    if not extract_images:
        return []

    images: List[ExtractedImage] = []
    media_list = data.get("mediaDetails") or data.get("photos") or []

    for media in media_list:
        if isinstance(media, dict):
            media_type = media.get("type", "")
            if media_type != "photo":
                continue
            media_url = media.get("media_url_https", "")
        elif isinstance(media, str):
            media_url = media
        else:
            continue

        if not media_url:
            continue

        from .http_fetch import fetch_image_bytes
        from .vision import make_image_marker

        img_bytes = fetch_image_bytes(media_url, verify_ssl=verify_ssl)
        if img_bytes:
            marker = make_image_marker()
            ext = "jpeg" if ".jpg" in media_url else "png"
            images.append(ExtractedImage(
                image_bytes=img_bytes,
                format=ext,
                source_label="Tweet media",
                position_marker=marker,
                alt_text="",
            ))

    return images


def fetch_tweet(
    url: str,
    twitter_config: "TwitterConfig",
    network_config: "NetworkConfig",
    auth_config: "Optional[object]" = None,
    extract_images: bool = False,
) -> Tuple[TweetInfo, List[ArticleSection], List[ExtractedImage]]:
    """Fetch a tweet via syndication API → oEmbed → Nitter cascade.

    Returns ``(TweetInfo, sections, images)`` where *images* is a list
    of ``ExtractedImage`` objects (non-empty only for syndication path
    with photos when *extract_images* is True).
    Raises ``TweetFetchError`` if all methods fail.
    """
    canonical = _normalize_tweet_url(url)
    tweet_id = _extract_tweet_id(canonical)
    errors: List[str] = []

    # --- 1. Syndication API (primary) ---
    if getattr(twitter_config, "syndication_enabled", True):
        try:
            data = _fetch_syndication_json(
                tweet_id,
                timeout=twitter_config.timeout,
                verify_ssl=twitter_config.verify_ssl,
            )
            info, sections = _parse_syndication_response(
                data, canonical, twitter_config, auth_config=auth_config)
            print("  [tweet] Fetched via syndication API (single tweet only)",
                  flush=True)
            # Extract media images from syndication data
            images = _extract_syndication_media(
                data, extract_images, verify_ssl=twitter_config.verify_ssl)
            if images and sections:
                # Append image markers to the last section's body
                markers = "\n\n".join(img.position_marker for img in images)
                last = sections[-1]
                sections[-1] = ArticleSection(
                    heading=last.heading,
                    level=last.level,
                    body=last.body + "\n\n" + markers,
                )
            return info, sections, images
        except _TweetTombstoneError:
            raise  # definitively gone — don't try other providers
        except TweetFetchError as exc:
            errors.append(f"Syndication: {exc}")
            print(f"  [tweet] Syndication failed: {exc}", flush=True)
        except Exception as exc:
            errors.append(f"Syndication: {exc}")
            print(f"  [tweet] Syndication error: {exc}", flush=True)

    # --- 2. oEmbed API (fallback) ---
    try:
        data = _fetch_oembed(
            canonical,
            timeout=twitter_config.timeout,
            verify_ssl=twitter_config.verify_ssl,
        )
        info, sections = _parse_oembed_response(data, canonical, twitter_config)
        print("  [tweet] Fetched via oEmbed API (single tweet only)",
              flush=True)
        return info, sections, []
    except TweetFetchError as exc:
        errors.append(f"oEmbed: {exc}")
        print(f"  [tweet] oEmbed failed: {exc}", flush=True)
    except Exception as exc:
        errors.append(f"oEmbed: {exc}")
        print(f"  [tweet] oEmbed error: {exc}", flush=True)

    # --- 3. Nitter (last resort) ---
    try:
        info, sections = fetch_tweet_via_nitter(
            url, twitter_config, network_config)
        return info, sections, []
    except TweetFetchError as exc:
        errors.append(f"Nitter: {exc}")
    except Exception as exc:
        errors.append(f"Nitter: {exc}")

    raise TweetFetchError(
        f"All tweet fetch methods failed for {canonical}:\n  - "
        + "\n  - ".join(errors)
    )
