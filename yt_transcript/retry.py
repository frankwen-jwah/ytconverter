"""Shared retry-with-backoff utility used by ytdlp and http_fetch."""

import time
from typing import Any, Callable, Optional, Tuple


def retry_with_backoff(
    fn: Callable[[], Any],
    retries: int,
    backoff_base: int = 2,
    classify_error: Optional[Callable[[Exception], Tuple]] = None,
) -> Any:
    """Execute *fn* with exponential-backoff retries.

    Parameters
    ----------
    fn : callable
        Zero-arg callable.  Called up to *retries* times.
    retries : int
        Maximum number of attempts (≥1).
    backoff_base : int
        Base for exponential wait: ``backoff_base ** attempt``.
    classify_error : callable, optional
        ``classify_error(exc)`` must return one of:
        - ``("fatal",)``  — re-raise immediately
        - ``("retry",)``  — retryable with standard backoff
        - ``("retry", wait_override)`` — retryable with custom wait (seconds)
        If *None*, every exception is retryable with standard backoff.

    Returns the value of *fn()* on success.
    Raises the last exception after all retries are exhausted.
    """
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            last_err = exc
            if classify_error is not None:
                verdict = classify_error(exc)
                if verdict[0] == "fatal":
                    raise
                wait = verdict[1] if len(verdict) > 1 else backoff_base ** attempt
            else:
                wait = backoff_base ** attempt

            if attempt < retries - 1:
                print(f"  Retrying in {wait}s... "
                      f"(attempt {attempt + 2}/{retries})")
                time.sleep(wait)
    raise last_err  # type: ignore[misc]
