"""Tweet/thread extraction pipeline — single-tweet orchestration and dry-run."""

from typing import TYPE_CHECKING

from .article import sections_to_body_text
from .exceptions import TweetFetchError, PipelineError
from .models import TweetResult
from .tweet import fetch_tweet

if TYPE_CHECKING:
    from .config import Config


def process_single_tweet(url: str, config: "Config") -> TweetResult:
    """Full extraction pipeline for one tweet/thread."""
    info, sections = fetch_tweet(
        url, config.twitter, config.network, auth_config=config.auth
    )
    body_text = sections_to_body_text(sections)
    print(f"{info.title}", flush=True)
    return TweetResult(info=info, body_text=body_text, sections=sections)


def dry_run_tweet(url: str, config: "Config") -> None:
    """Print tweet metadata without full extraction."""
    try:
        info, _sections = fetch_tweet(
            url, config.twitter, config.network, auth_config=config.auth
        )
        print(f"  Author:  {info.author} ({info.author_name})", flush=True)
        print(f"  Date:    {info.publish_date}", flush=True)
        print(f"  Thread:  {info.thread_length} post(s)", flush=True)
        print(f"  Words:   {info.word_count}", flush=True)
        preview = info.title[:120]
        print(f"  Preview: {preview}", flush=True)
        print()
    except PipelineError as e:
        print(f"  ERROR: {e}")
        print()
