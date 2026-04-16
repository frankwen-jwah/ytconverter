"""Exception hierarchy for the content extraction pipeline."""


class PipelineError(Exception):
    """Base exception for this pipeline."""


class NoSubtitlesError(PipelineError):
    """Video has no subtitles available."""


class AuthRequiredError(PipelineError):
    """Video requires authentication but no cookies provided."""


class VideoUnavailableError(PipelineError):
    """Video is private, deleted, or region-locked."""


class NetworkError(PipelineError):
    """Network error after all retries exhausted."""


class WhisperError(PipelineError):
    """Audio transcription with Whisper failed."""


class LLMError(PipelineError):
    """LLM API call for polish failed."""


class ArticleFetchError(PipelineError):
    """HTTP request to fetch article failed (non-retryable)."""


class ContentExtractionError(PipelineError):
    """Could not extract meaningful content from HTML."""


class PDFExtractionError(PipelineError):
    """Could not extract meaningful content from PDF."""


class ArxivAPIError(PipelineError):
    """ArXiv API request failed or returned unexpected data."""


class LocalFileError(PipelineError):
    """Could not read or extract content from a local file."""


class PodcastFetchError(PipelineError):
    """Could not fetch or parse podcast feed/episode."""


class TweetFetchError(PipelineError):
    """Could not fetch or extract tweet/thread content."""


class MarkItDownError(PipelineError):
    """MarkItDown conversion failed for a file."""
