"""yt-dlp subprocess interaction — run commands, fetch metadata, resolve URLs."""

import json
import subprocess
from typing import List

from .exceptions import (
    AuthRequiredError,
    NetworkError,
    VideoUnavailableError,
    YTTranscriptError,
)
from .retry import retry_with_backoff


def _classify_ytdlp_error(exc: Exception):
    """Classify a yt-dlp subprocess error for the retry loop."""
    if not isinstance(exc, _YtdlpSubprocessError):
        return ("fatal",)
    stderr = exc.stderr.lower()
    # Fatal: video unavailable
    if any(p in stderr for p in ["video unavailable", "private video",
                                  "this video has been removed"]):
        raise VideoUnavailableError(exc.stderr.strip()) from exc
    # Fatal: auth required
    if any(p in stderr for p in ["sign in", "requires authentication",
                                  "members-only", "member",
                                  "join this channel"]):
        raise AuthRequiredError(
            "This video requires authentication. Use --cookies-from-browser chrome"
        ) from exc
    # Retryable: network error
    if any(p in stderr for p in ["unable to download", "http error",
                                  "connection", "urlopen error", "timed out"]):
        is_rate_limit = "429" in stderr or "too many requests" in stderr
        if is_rate_limit:
            return ("retry", None)  # wait computed below
        return ("retry",)
    # Unknown error — fatal
    raise YTTranscriptError(
        exc.stderr.strip() or f"yt-dlp exited with code {exc.returncode}"
    ) from exc


class _YtdlpSubprocessError(Exception):
    """Internal wrapper carrying stderr/returncode for classification."""
    def __init__(self, stderr: str, returncode: int):
        super().__init__(stderr)
        self.stderr = stderr
        self.returncode = returncode


def run_ytdlp(args: List[str], cookie_args: List[str], retries: int = 3,
              backoff_base: int = 2) -> subprocess.CompletedProcess:
    """Run yt-dlp with given args. Retries on network errors."""
    cmd = ["yt-dlp"] + cookie_args + args
    _last_result = [None]

    def _attempt():
        result = subprocess.run(cmd, capture_output=True, text=True)
        _last_result[0] = result
        if result.returncode == 0:
            return result
        raise _YtdlpSubprocessError(result.stderr, result.returncode)

    def _classify(exc):
        verdict = _classify_ytdlp_error(exc)
        if verdict[0] == "retry" and verdict[1] is None:
            # Rate limit — escalating wait
            return ("retry",)  # use default backoff, but we override below
        return verdict

    try:
        return retry_with_backoff(_attempt, retries, backoff_base, _classify)
    except _YtdlpSubprocessError as exc:
        raise NetworkError(
            f"Network error after {retries} attempts: {exc.stderr.strip()}"
        ) from exc


def fetch_video_metadata(url: str, cookie_args: List[str], retries: int = 3,
                         backoff_base: int = 2) -> dict:
    """Fetch video metadata as JSON dict."""
    result = run_ytdlp(
        ["--dump-json", "--skip-download", "--no-warnings", url],
        cookie_args, retries, backoff_base=backoff_base
    )
    # yt-dlp may output multiple JSON objects (one per line) for playlists;
    # we only need the first entry.
    first_line = result.stdout.split("\n", 1)[0]
    return json.loads(first_line)


def resolve_urls(raw_urls: List[str], cookie_args: List[str]) -> List[str]:
    """Expand playlist/channel URLs to individual video URLs. Deduplicate."""
    video_urls = []
    seen = set()
    for url in raw_urls:
        url = url.strip()
        if not url or url.startswith("#"):
            continue
        # Check if this is a playlist or channel
        if "playlist" in url or "/c/" in url or "/channel/" in url or "/@" in url:
            try:
                result = run_ytdlp(
                    ["--flat-playlist", "--dump-json", "--no-warnings", url],
                    cookie_args, retries=2
                )
                for line in result.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    vid_url = entry.get("url") or entry.get("webpage_url", "")
                    if vid_url and "watch" not in vid_url:
                        vid_url = f"https://www.youtube.com/watch?v={entry.get('id', '')}"
                    if vid_url and vid_url not in seen:
                        seen.add(vid_url)
                        video_urls.append(vid_url)
            except YTTranscriptError as e:
                print(f"  Warning: could not expand {url}: {e}")
                # Try as single video
                if url not in seen:
                    seen.add(url)
                    video_urls.append(url)
        else:
            if url not in seen:
                seen.add(url)
                video_urls.append(url)
    return video_urls
