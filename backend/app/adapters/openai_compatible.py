import httpx

from app.adapters.base import ProviderAdapter, UpstreamError


class OpenAICompatibleAdapter(ProviderAdapter):
    """Adapter for any provider that speaks the OpenAI chat-completions wire format
    (the mock providers, and any real OpenAI-compatible provider)."""

    def __init__(self, name: str, base_url: str, api_key: str):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def chat_completion(self, model: str, messages: list[dict], timeout: float) -> dict:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": model, "messages": messages},
                )
        except httpx.TimeoutException as exc:
            raise UpstreamError(f"{self.name} timed out after {timeout}s") from exc
        except httpx.RequestError as exc:
            raise UpstreamError(f"{self.name} request failed: {exc}") from exc

        if response.status_code >= 400:
            raise UpstreamError(
                f"{self.name} returned {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        return response.json()

    async def stream_chat_completion(self, model: str, messages: list[dict], timeout: float):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": model, "messages": messages, "stream": True},
                ) as response:
                    if response.status_code >= 400:
                        raw = await response.aread()
                        raise UpstreamError(
                            f"{self.name} returned {response.status_code}: {raw[:200]!r}",
                            status_code=response.status_code,
                        )
                    async for line in response.aiter_lines():
                        if line:
                            yield line
        except httpx.TimeoutException as exc:
            raise UpstreamError(f"{self.name} timed out after {timeout}s") from exc
        except httpx.RequestError as exc:
            raise UpstreamError(f"{self.name} request failed: {exc}") from exc
