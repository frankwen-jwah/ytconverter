"""Exception hierarchy for the YouTube transcript pipeline."""


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
