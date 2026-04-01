"""Podcast extraction pipeline — single-episode orchestration and dry-run."""

import pathlib
import tempfile
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from .exceptions import PodcastFetchError, YTTranscriptError
from .models import PodcastResult
from .podcast import (
    extract_podcast_info_from_rss,
    extract_podcast_info_from_ytdlp,
    parse_rss_feed,
)
from .url_detect import is_rss_feed_url

if TYPE_CHECKING:
    from .config import Config


def resolve_podcast_feed(
    feed_url: str, config: "Config",
) -> List[Tuple[str, Dict]]:
    """Expand a podcast feed URL to a list of ``(audio_url, episode_meta)`` pairs.

    For RSS feed URLs, uses feedparser. For platform URLs (Apple Podcasts, etc.),
    tries yt-dlp first.

    Returns a list suitable for the CLI dispatch loop.
    """
    # RSS feeds — use feedparser
    if is_rss_feed_url(feed_url) or config.podcast.prefer_rss:
        try:
            episodes = parse_rss_feed(feed_url, config)
            if episodes:
                print(f"  [podcast] Found {len(episodes)} episode(s) from RSS feed",
                      flush=True)
                return [(ep["audio_url"], ep) for ep in episodes]
        except PodcastFetchError:
            pass  # Fall through to yt-dlp

    # Platform URLs — try yt-dlp
    try:
        from .deps import ensure_yt_dlp
        ensure_yt_dlp()
        from .ytdlp import run_ytdlp
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = pathlib.Path(tmpdir) / "meta.json"
            args = [
                "--dump-json", "--flat-playlist",
                "--no-warnings",
                "-o", str(out_path),
                feed_url,
            ]
            result = run_ytdlp(args, cookie_args=[], retries=2)
            # yt-dlp prints one JSON per line for playlists
            entries = []
            for line in result.stdout.splitlines() if result.stdout else []:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            # If run_ytdlp doesn't return stdout directly, try reading the dump
            if not entries:
                # yt-dlp --dump-json writes to stdout, captured by run_ytdlp
                # Fallback: treat the feed as a single episode
                return [(feed_url, {"title": "Episode", "show_name": "Podcast",
                                    "audio_url": feed_url, "url": feed_url,
                                    "publish_date": "unknown", "duration_seconds": 0,
                                    "description": "", "language": None,
                                    "episode_number": None})]

            max_eps = config.podcast.max_episodes
            if max_eps > 0:
                entries = entries[:max_eps]
            result_list = []
            for meta in entries:
                audio_url = meta.get("url", meta.get("webpage_url", feed_url))
                result_list.append((audio_url, meta))
            return result_list
    except Exception as exc:
        print(f"  [podcast] yt-dlp resolution failed: {exc}", flush=True)

    # Last resort: treat the URL as a single episode audio URL
    return [(feed_url, {"title": "Episode", "show_name": "Podcast",
                        "audio_url": feed_url, "url": feed_url,
                        "publish_date": "unknown", "duration_seconds": 0,
                        "description": "", "language": None,
                        "episode_number": None})]


def process_single_podcast(
    url: str,
    cookie_args: List[str],
    config: "Config",
    episode_meta: Optional[Dict] = None,
) -> PodcastResult:
    """Full extraction pipeline for one podcast episode.

    1. Build episode metadata (from RSS dict or yt-dlp)
    2. Download audio via Whisper pipeline
    3. Transcribe via Whisper
    4. Return PodcastResult with cues
    """
    from .whisper import download_audio, transcribe_audio

    # --- 1. Build metadata ---
    if episode_meta and "audio_url" in episode_meta:
        info = extract_podcast_info_from_rss(episode_meta)
        audio_url = episode_meta["audio_url"]
    else:
        # Try yt-dlp metadata extraction
        try:
            from .ytdlp import fetch_video_metadata
            meta = fetch_video_metadata(url, cookie_args,
                                        retries=config.network.retries,
                                        backoff_base=config.network.backoff_base)
            info = extract_podcast_info_from_ytdlp(meta)
            audio_url = url
        except Exception as exc:
            raise PodcastFetchError(
                f"Could not extract podcast metadata from {url}: {exc}"
            ) from exc

    print(f"{info.title}", flush=True)
    if info.show_name:
        print(f"  [podcast] Show: {info.show_name}", flush=True)

    # --- 2. Download audio ---
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="podcast_"))
    try:
        print(f"  [podcast] Downloading audio...", flush=True)
        audio_path = download_audio(
            audio_url, cookie_args, tmpdir,
            retries=config.network.retries,
            audio_quality=config.whisper.audio_quality,
            backoff_base=config.network.backoff_base,
        )

        # --- 3. Transcribe ---
        print(f"  [podcast] Transcribing with Whisper ({config.whisper.model})...",
              flush=True)
        cues, detected_lang = transcribe_audio(
            audio_path,
            lang_hint=info.language,
            model_name=config.whisper.model,
            device_override=config.whisper.device if config.whisper.device != "auto" else None,
            beam_size=config.whisper.beam_size,
            vad_filter=config.whisper.vad_filter,
        )
        print(f"  [podcast] Transcribed: {len(cues)} cues, language={detected_lang}",
              flush=True)

        return PodcastResult(
            info=info,
            cues=cues,
            sub_language=detected_lang,
            is_whisper_transcribed=True,
        )
    finally:
        # Cleanup temp audio files
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def dry_run_podcast(url: str, cookie_args: List[str], config: "Config") -> None:
    """Print podcast metadata without downloading audio."""
    try:
        if is_rss_feed_url(url) or config.podcast.prefer_rss:
            try:
                episodes = parse_rss_feed(url, config)
                show_name = episodes[0]["show_name"] if episodes else "Unknown"
                print(f"  Feed:     {show_name}", flush=True)
                print(f"  Episodes: {len(episodes)}", flush=True)
                for i, ep in enumerate(episodes[:5], 1):
                    from .metadata import format_duration
                    dur = format_duration(ep.get("duration_seconds", 0))
                    print(f"    {i}. {ep['title']} ({dur})", flush=True)
                if len(episodes) > 5:
                    print(f"    ... and {len(episodes) - 5} more", flush=True)
                print()
                return
            except PodcastFetchError:
                pass

        # yt-dlp fallback
        from .ytdlp import fetch_video_metadata
        meta = fetch_video_metadata(url, cookie_args,
                                    retries=config.network.retries,
                                    backoff_base=config.network.backoff_base)
        info = extract_podcast_info_from_ytdlp(meta)
        print(f"  Title:    {info.title}", flush=True)
        print(f"  Show:     {info.show_name}", flush=True)
        print(f"  Date:     {info.publish_date}", flush=True)
        print(f"  Duration: {info.duration_string}", flush=True)
        print()
    except YTTranscriptError as e:
        print(f"  ERROR: {e}")
        print()
