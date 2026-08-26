import json
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from app.config import get_settings

ResultT = TypeVar("ResultT", bound=BaseModel)


class LLMClient(Protocol):
    provider: str
    model: str

    async def structured(self, prompt: str, result_type: type[ResultT]) -> ResultT: ...

    async def search_structured(self, prompt: str, result_type: type[ResultT]) -> ResultT: ...

    async def text(self, prompt: str) -> str: ...


class GoogleLLMClient:
    provider = "google"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required")
        self.api_key = settings.gemini_api_key
        self.model = settings.llm_model
        self.url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )

    async def _generate(
        self, prompt: str, schema: dict[str, Any] | None = None, *, google_search: bool = False
    ) -> str:
        generation_config: dict[str, Any] = {}
        if schema:
            generation_config = {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            }
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if google_search:
            payload["tools"] = [{"googleSearch": {}}, {"urlContext": {}}]
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self.url, params={"key": self.api_key}, json=payload)
            response.raise_for_status()
        data = response.json()
        candidate = data["candidates"][0]
        if google_search and not candidate.get("groundingMetadata"):
            raise ValueError("Gemini response was not grounded by Google Search")
        return candidate["content"]["parts"][0]["text"]

    async def structured(self, prompt: str, result_type: type[ResultT]) -> ResultT:
        raw = await self._generate(prompt, result_type.model_json_schema())
        return result_type.model_validate(json.loads(raw))

    async def search_structured(self, prompt: str, result_type: type[ResultT]) -> ResultT:
        raw = await self._generate(
            prompt, result_type.model_json_schema(), google_search=True
        )
        return result_type.model_validate(json.loads(raw))

    async def text(self, prompt: str) -> str:
        return await self._generate(prompt)


def get_llm_client() -> LLMClient:
    return GoogleLLMClient()

