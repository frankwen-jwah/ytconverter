"""HTTP fetching — download web pages with retry, UA rotation, and SSL handling."""

import random
from typing import TYPE_CHECKING

from .exceptions import ArticleFetchError, NetworkError
from .retry import retry_with_backoff

if TYPE_CHECKING:
    from .config import ArticlesConfig, NetworkConfig

# Realistic User-Agent pool (inspired by x-crawl fingerprints)
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


def _build_headers(rotate_ua: bool = True) -> dict:
    """Return browser-like request headers."""
    ua = random.choice(_USER_AGENTS) if rotate_ua else _USER_AGENTS[0]
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _classify_http_error(exc: Exception):
    """Classify HTTP/connection errors for the retry loop."""
    import requests

    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else 0
        if code == 429:
            return ("retry",)  # rate-limited — standard backoff
        if 400 <= code < 500:
            raise ArticleFetchError(
                f"HTTP {code}: {exc.response.reason}"
            ) from exc
        # 5xx — retryable
        return ("retry",)

    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return ("retry",)

    # Unknown request error — fatal
    return ("fatal",)


def fetch_html(url: str, articles_config: "ArticlesConfig",
               network_config: "NetworkConfig") -> str:
    """Fetch HTML content from *url* with retry and backoff.

    Returns the decoded HTML string.
    Raises ``ArticleFetchError`` for non-retryable HTTP errors.
    Raises ``NetworkError`` after all retries are exhausted.
    """
    from .deps import ensure_requests
    ensure_requests()
    import requests

    headers = _build_headers(articles_config.user_agent_rotation)

    def _attempt():
        resp = requests.get(
            url,
            headers=headers,
            timeout=articles_config.timeout,
            verify=articles_config.verify_ssl,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    try:
        return retry_with_backoff(
            _attempt,
            retries=network_config.retries,
            backoff_base=network_config.backoff_base,
            classify_error=_classify_http_error,
        )
    except ArticleFetchError:
        raise
    except Exception as exc:
        raise NetworkError(
            f"Failed to fetch {url} after {network_config.retries} attempts: {exc}"
        ) from exc
