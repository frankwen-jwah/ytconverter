"""Proactive rate limiter for Azure OpenAI with sliding-window tracking."""

import logging
import threading
import time
from typing import List, Tuple

from .config import RateLimitConfig

_log = logging.getLogger("content_extractor.rate_limiter")

WINDOW_SECONDS = 60.0  # Sliding window duration


class RateLimiter:
    """Proactive rate limiter for Azure OpenAI.

    Tracks sliding-window token and request usage per minute.
    When usage approaches *threshold* of the limits, blocks callers
    until the window slides enough to create headroom.
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self._tpm = config.tpm
        self._rpm = config.rpm
        self._threshold = config.threshold
        self._lock = threading.Lock()
        self._token_log: List[Tuple[float, int]] = []   # (timestamp, token_count)
        self._request_log: List[float] = []              # timestamps

    def _prune_window(self, now: float) -> Tuple[int, int]:
        """Remove entries older than WINDOW_SECONDS. Return (tokens_used, requests_used)."""
        cutoff = now - WINDOW_SECONDS
        self._token_log = [(t, n) for t, n in self._token_log if t > cutoff]
        self._request_log = [t for t in self._request_log if t > cutoff]
        tokens_used = sum(n for _, n in self._token_log)
        return tokens_used, len(self._request_log)

    def _wait_time(self, tokens_used: int, requests_used: int,
                   estimated_tokens: int) -> float:
        """Calculate seconds to wait until usage drops below threshold."""
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS
        max_wait = 0.0

        # Token-based wait
        token_limit = self._tpm * self._threshold
        if tokens_used + estimated_tokens > token_limit:
            # Find how many old tokens need to age out
            excess = (tokens_used + estimated_tokens) - token_limit
            aged = 0
            for ts, count in sorted(self._token_log):
                if ts <= cutoff:
                    continue
                aged += count
                if aged >= excess:
                    max_wait = max(max_wait, ts - cutoff + 0.1)
                    break
            else:
                max_wait = max(max_wait, WINDOW_SECONDS)

        # Request-based wait
        req_limit = int(self._rpm * self._threshold)
        if requests_used + 1 > req_limit:
            sorted_reqs = sorted(self._request_log)
            excess_count = (requests_used + 1) - req_limit
            if excess_count <= len(sorted_reqs):
                oldest_to_expire = sorted_reqs[excess_count - 1]
                max_wait = max(max_wait, oldest_to_expire - cutoff + 0.1)
            else:
                max_wait = max(max_wait, WINDOW_SECONDS)

        return max_wait

    def acquire(self, estimated_tokens: int) -> None:
        """Block until both TPM and RPM have headroom for this request."""
        while True:
            with self._lock:
                now = time.monotonic()
                tokens_used, requests_used = self._prune_window(now)
                wait = self._wait_time(tokens_used, requests_used, estimated_tokens)
                if wait <= 0:
                    # Reserve a slot in the request log immediately
                    self._request_log.append(now)
                    return

            _log.info("Rate limiter: waiting %.1fs (tokens=%d/%d, requests=%d/%d)",
                      wait, tokens_used, self._tpm, requests_used, self._rpm)
            print(f"  [rate-limit] Throttling {wait:.1f}s "
                  f"(tokens: {tokens_used}/{self._tpm}, "
                  f"requests: {requests_used}/{self._rpm})", flush=True)
            time.sleep(wait)

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Record actual token usage after a call completes."""
        total = prompt_tokens + completion_tokens
        with self._lock:
            self._token_log.append((time.monotonic(), total))


def estimate_tokens(text: str, is_cjk: bool = False) -> int:
    """Rough token estimate: ~4 chars/token for English, ~1.5 for CJK."""
    if is_cjk:
        return max(1, int(len(text) / 1.5))
    return max(1, len(text) // 4)
