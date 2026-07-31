from abc import ABC, abstractmethod
from typing import Any


class UpstreamError(Exception):
    """A provider call failed (non-2xx, timeout, or malformed response)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProviderAdapter(ABC):
    """Keeps provider wire details (base URL, auth) out of routing/business logic."""

    name: str

    @abstractmethod
    async def chat_completion(
        self, model: str, messages: list[dict], timeout: float
    ) -> dict[str, Any]:
        """Non-streaming chat completion. Returns the OpenAI-shaped response body."""
        raise NotImplementedError

    @abstractmethod
    async def stream_chat_completion(self, model: str, messages: list[dict], timeout: float):
        """Yields raw SSE 'data: ...' lines (no trailing newlines) as they arrive.

        Raises UpstreamError if the connection/handshake itself fails - callers may
        retry or fail over in that case. Once the first line has been yielded,
        any further failure must be treated as a mid-stream failure, not retried."""
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator function
