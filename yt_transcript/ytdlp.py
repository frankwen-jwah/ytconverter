"""yt-dlp subprocess interaction — run commands, fetch metadata, resolve URLs."""

import json
import subprocess
import time
from typing import List

from .exceptions import (
    AuthRequiredError,
    NetworkError,
    VideoUnavailableError,
    YTTranscriptError,
)


def run_ytdlp(args: List[str], cookie_args: List[str], retries: int = 3,
              backoff_base: int = 2) -> subprocess.CompletedProcess:
    """Run yt-dlp with given args. Retries on network errors."""
    cmd = ["yt-dlp"] + cookie_args + args
    last_err = None
    for attempt in range(retries):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result
        stderr = result.stderr.lower()
        # Classify error
        if any(p in stderr for p in ["video unavailable", "private video", "this video has been removed"]):
            raise VideoUnavailableError(result.stderr.strip())
        if any(p in stderr for p in ["sign in", "requires authentication", "members-only", "member", "join this channel"]):
            raise AuthRequiredError(
                "This video requires authentication. Use --cookies-from-browser chrome"
            )
        if any(p in stderr for p in ["unable to download", "http error", "connection", "urlopen error", "timed out"]):
            last_err = result.stderr.strip()
            if attempt < retries - 1:
                is_rate_limit = "429" in stderr or "too many requests" in stderr
                wait = backoff_base ** attempt
                if is_rate_limit:
                    wait = max(wait, 10) * (attempt + 1)  # 10s, 20s, 30s, ...
                print(f"  {'Rate limited' if is_rate_limit else 'Network error'}, "
                      f"retrying in {wait}s... (attempt {attempt+2}/{retries})")
                time.sleep(wait)
                continue
            raise NetworkError(f"Network error after {retries} attempts: {last_err}")
        # Unknown error
        raise YTTranscriptError(result.stderr.strip() or f"yt-dlp exited with code {result.returncode}")
    raise NetworkError(f"Failed after {retries} attempts: {last_err}")


def fetch_video_metadata(url: str, cookie_args: List[str], retries: int = 3,
                         backoff_base: int = 2) -> dict:
    """Fetch video metadata as JSON dict."""
    result = run_ytdlp(
        ["--dump-json", "--skip-download", "--no-warnings", url],
        cookie_args, retries, backoff_base=backoff_base
    )
    return json.loads(result.stdout)


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
