"""Exception hierarchy for the content extraction pipeline."""


class YTTranscriptError(Exception):
    """Base exception for this pipeline."""


class NoSubtitlesError(YTTranscriptError):
    """Video has no subtitles available."""


class AuthRequiredError(YTTranscriptError):
    """Video requires authentication but no cookies provided."""


class VideoUnavailableError(YTTranscriptError):
    """Video is private, deleted, or region-locked."""


class NetworkError(YTTranscriptError):
    """Network error after all retries exhausted."""


class WhisperError(YTTranscriptError):
    """Audio transcription with Whisper failed."""


class LLMError(YTTranscriptError):
    """LLM API call for polish/summarize failed."""


class ArticleFetchError(YTTranscriptError):
    """HTTP request to fetch article failed (non-retryable)."""


class ContentExtractionError(YTTranscriptError):
    """Could not extract meaningful content from HTML."""


class PDFExtractionError(YTTranscriptError):
    """Could not extract meaningful content from PDF."""


class ArxivAPIError(YTTranscriptError):
    """ArXiv API request failed or returned unexpected data."""
