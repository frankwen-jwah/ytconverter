"""Unified LLM backend — Azure OpenAI API with rate limiting."""

import base64
import logging
import os
import time
from typing import Optional, TYPE_CHECKING

from .exceptions import LLMError
from .rate_limiter import RateLimiter, estimate_tokens

if TYPE_CHECKING:
    from .config import Config

_log = logging.getLogger("content_extractor.llm_backend")

# ---------------------------------------------------------------------------
# Module state — initialized by init_backend()
# ---------------------------------------------------------------------------

_client = None       # AzureOpenAI instance
_deployment = None   # Default deployment name
_rate_limiter: Optional[RateLimiter] = None
_config: Optional["Config"] = None
_timeout: int = 120


def init_backend(config: "Config") -> None:
    """Load .env, create AzureOpenAI client, init rate limiter.

    Reads credentials from environment variables:
      AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_BASE,
      AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT
    """
    global _client, _deployment, _rate_limiter, _config, _timeout

    _config = config
    _timeout = config.llm.timeout

    # Load .env from project root
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # dotenv not required if env vars already set

    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    api_base = os.environ.get("AZURE_OPENAI_API_BASE")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
    _deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")

    if not api_key or not api_base:
        raise LLMError(
            "Azure OpenAI credentials not found. Set AZURE_OPENAI_API_KEY and "
            "AZURE_OPENAI_API_BASE in .env file."
        )
    if not _deployment:
        raise LLMError(
            "Azure OpenAI deployment not set. Set AZURE_OPENAI_DEPLOYMENT in .env file."
        )

    # Ensure openai package is available
    from .deps import ensure_openai
    ensure_openai()

    from openai import AzureOpenAI
    _client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=api_base,
        api_version=api_version,
    )

    # Initialize rate limiter
    _rate_limiter = RateLimiter(config.llm.rate_limit)

    print(f"  Azure OpenAI backend initialized (deployment: {_deployment})",
          flush=True)


def chat_completion(system: str, user_msg: str,
                    model: Optional[str] = None) -> str:
    """Text chat completion via Azure OpenAI with rate limiting.

    Returns the assistant's response text.
    """
    if _client is None:
        raise LLMError("LLM backend not initialized. Call init_backend() first.")

    deployment = model or _deployment
    est_tokens = estimate_tokens(system + user_msg)

    if _rate_limiter:
        _rate_limiter.acquire(est_tokens)

    t0 = time.time()
    try:
        response = _client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            timeout=_timeout,
        )
    except Exception as e:
        _log.error("Azure OpenAI chat error: %s", e)
        raise LLMError(f"Azure OpenAI API error: {e}") from e

    elapsed = time.time() - t0

    # Record actual token usage
    usage = response.usage
    if usage and _rate_limiter:
        _rate_limiter.record(usage.prompt_tokens, usage.completion_tokens)
        _log.info("chat_completion: %d prompt + %d completion tokens, %.1fs",
                  usage.prompt_tokens, usage.completion_tokens, elapsed)

    result = response.choices[0].message.content or ""

    # Check for known error patterns
    if _config:
        for pattern in _config.llm.error_patterns:
            if pattern.lower() in result.lower():
                raise LLMError(f"LLM returned error pattern: {pattern}")

    return result


def vision_completion(system: str, user_msg: str,
                      image_bytes: bytes, mime_type: str = "image/png",
                      model: Optional[str] = None) -> str:
    """Vision completion — sends image as base64 in content array.

    Returns the assistant's description text.
    """
    if _client is None:
        raise LLMError("LLM backend not initialized. Call init_backend() first.")

    deployment = model or _deployment
    b64_data = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime_type};base64,{b64_data}"

    # Estimate tokens: image ~85 tokens base + text tokens
    est_tokens = estimate_tokens(system + user_msg) + 85

    if _rate_limiter:
        _rate_limiter.acquire(est_tokens)

    t0 = time.time()
    try:
        response = _client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": user_msg},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]},
            ],
            timeout=_timeout,
        )
    except Exception as e:
        _log.error("Azure OpenAI vision error: %s", e)
        raise LLMError(f"Azure OpenAI vision API error: {e}") from e

    elapsed = time.time() - t0

    usage = response.usage
    if usage and _rate_limiter:
        _rate_limiter.record(usage.prompt_tokens, usage.completion_tokens)
        _log.info("vision_completion: %d prompt + %d completion tokens, %.1fs",
                  usage.prompt_tokens, usage.completion_tokens, elapsed)

    return response.choices[0].message.content or ""


def get_client():
    """Return the raw AzureOpenAI client (for MarkItDown integration)."""
    return _client


def get_deployment() -> Optional[str]:
    """Return the default deployment name."""
    return _deployment
