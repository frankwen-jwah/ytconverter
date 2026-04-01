"""Podcast extraction — parse RSS feeds and extract episode metadata."""

import re
from typing import Dict, List, TYPE_CHECKING

from .exceptions import PodcastFetchError
from .metadata import format_duration, parse_upload_date

if TYPE_CHECKING:
    from .config import Config, PodcastConfig
    from .models import PodcastEpisodeInfo


def _parse_itunes_duration(raw: str) -> int:
    """Parse iTunes duration to seconds. Accepts H:MM:SS, MM:SS, or raw seconds."""
    if not raw:
        return 0
    raw = str(raw).strip()
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(float(raw))
    except (ValueError, TypeError):
        return 0


def parse_rss_feed(feed_url: str, config: "Config") -> List[Dict]:
    """Fetch and parse an RSS feed, returning a list of episode metadata dicts.

    Each dict has: title, show_name, episode_number, url, audio_url,
    publish_date, duration_seconds, description, language.

    Raises ``PodcastFetchError`` on failure.
    """
    from .deps import ensure_feedparser, ensure_requests
    ensure_feedparser()
    ensure_requests()
    import feedparser
    import requests

    # feedparser can parse a URL directly, but we fetch manually for
    # better timeout/SSL control via our http stack.
    try:
        headers = {"User-Agent": "yt-transcript-podcast/1.0"}
        resp = requests.get(
            feed_url, headers=headers,
            timeout=30,
            verify=True,
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        raise PodcastFetchError(f"Failed to fetch RSS feed {feed_url}: {exc}") from exc

    if feed.bozo and not feed.entries:
        raise PodcastFetchError(
            f"Could not parse RSS feed {feed_url}: {feed.bozo_exception}"
        )

    show_name = feed.feed.get("title", "Unknown Podcast")
    show_language = feed.feed.get("language")

    episodes = []
    max_eps = config.podcast.max_episodes
    entries = feed.entries[:max_eps] if max_eps > 0 else feed.entries

    for entry in entries:
        # Find audio enclosure
        audio_url = ""
        for enc in entry.get("enclosures", []):
            if enc.get("type", "").startswith("audio/") or enc.get("href", "").endswith(
                (".mp3", ".m4a", ".ogg", ".wav", ".aac")
            ):
                audio_url = enc.get("href", "")
                break
        # Fallback: links with audio type
        if not audio_url:
            for link in entry.get("links", []):
                if link.get("type", "").startswith("audio/"):
                    audio_url = link.get("href", "")
                    break

        if not audio_url:
            continue  # Skip episodes without audio

        # Parse date
        pub_date = "unknown"
        if entry.get("published_parsed"):
            try:
                import time as _time
                pub_date = _time.strftime("%Y-%m-%d", entry.published_parsed)
            except Exception:
                pass
        if pub_date == "unknown" and entry.get("published"):
            try:
                from email.utils import parsedate_to_datetime
                pub_date = parsedate_to_datetime(entry.published).strftime("%Y-%m-%d")
            except Exception:
                pass  # pub_date stays "unknown"

        # Parse duration
        duration_raw = entry.get("itunes_duration", "") or entry.get("duration", "")
        duration_secs = _parse_itunes_duration(duration_raw)

        # Episode number
        ep_num = entry.get("itunes_episode") or entry.get("episode")
        ep_num = str(ep_num) if ep_num else None

        episodes.append({
            "title": entry.get("title", "Untitled Episode"),
            "show_name": show_name,
            "episode_number": ep_num,
            "url": entry.get("link", audio_url),
            "audio_url": audio_url,
            "publish_date": pub_date,
            "duration_seconds": duration_secs,
            "description": entry.get("summary", ""),
            "language": show_language,
        })

    return episodes


def extract_podcast_info_from_rss(meta: Dict) -> "PodcastEpisodeInfo":
    """Build a ``PodcastEpisodeInfo`` from an RSS episode metadata dict."""
    from .models import PodcastEpisodeInfo

    dur = int(meta.get("duration_seconds", 0))
    return PodcastEpisodeInfo(
        title=meta.get("title", "Untitled Episode"),
        show_name=meta.get("show_name", "Unknown Podcast"),
        episode_number=meta.get("episode_number"),
        url=meta.get("url", meta.get("audio_url", "")),
        audio_url=meta.get("audio_url", ""),
        publish_date=meta.get("publish_date", "unknown"),
        duration_seconds=dur,
        duration_string=format_duration(dur),
        language=meta.get("language"),
        description=meta.get("description", ""),
    )


def extract_podcast_info_from_ytdlp(meta: dict) -> "PodcastEpisodeInfo":
    """Build a ``PodcastEpisodeInfo`` from yt-dlp JSON metadata."""
    from .models import PodcastEpisodeInfo

    dur = int(meta.get("duration") or 0)
    return PodcastEpisodeInfo(
        title=meta.get("title", "Untitled Episode"),
        show_name=meta.get("series", meta.get("album", meta.get("channel", "Unknown Podcast"))),
        episode_number=str(meta["episode_number"]) if meta.get("episode_number") else None,
        url=meta.get("webpage_url", meta.get("original_url", "")),
        audio_url=meta.get("url", ""),
        publish_date=parse_upload_date(meta.get("upload_date", "")),
        duration_seconds=dur,
        duration_string=format_duration(dur),
        language=meta.get("language"),
        description=meta.get("description", ""),
    )
