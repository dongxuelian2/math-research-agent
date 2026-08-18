"""Core cancellation and archive contracts used by the Gemini engine."""

from ._base import Interrupted, StreamingUnavailable, is_transient_error


class LLMClient:
    """Marker for clients that expose the core engine's optional tool hook."""


__all__ = ["LLMClient", "Interrupted", "StreamingUnavailable", "is_transient_error"]
