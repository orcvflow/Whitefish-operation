"""
AI Model Adapter for Fundamental Analysis

Supports OpenRouter with Ling-3.0-flash-Fin model.
Configurable adapter pattern for easy model swapping.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Any

import httpx
from pydantic import BaseModel, Field

from ..canonical import Instrument, QualityStatus
from ..config import AIConfig

logger = logging.getLogger(__name__)


class AIResponse(BaseModel):
    """Structured AI response."""
    content: str
    model: str
    provider: str
    usage: dict[str, int] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AIAdapter:
    """
    Base AI adapter interface.
    
    All AI adapters must implement analyze() method.
    """

    def __init__(self, config: AIConfig):
        self.config = config

    async def analyze(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> AIResponse:
        """Analyze prompt and return structured response."""
        raise NotImplementedError

    async def close(self) -> None:
        """Cleanup resources."""
        pass


class OpenRouterAdapter(AIAdapter):
    """
    OpenRouter AI adapter for Ling-3.0-flash-Fin.
    
    Uses OpenAI-compatible API at https://openrouter.ai/api/v1
    """

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        config: AIConfig,
        api_key: str,
    ):
        super().__init__(config)
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": "https://trading-mvp.local",
                    "X-Title": "Trading MVP",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def analyze(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> AIResponse:
        """Analyze via OpenRouter API."""
        client = await self._ensure_client()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "top_p": self.config.top_p,
            "top_k": self.config.top_k,
            "max_tokens": max_tokens if max_tokens else self.config.max_tokens,
        }

        if response_format:
            payload["response_format"] = response_format

        last_error: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await client.post(
                    f"{self.BASE_URL}/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                choice = data.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content", "")

                return AIResponse(
                    content=content,
                    model=data.get("model", self.config.model),
                    provider="openrouter",
                    usage=data.get("usage", {}),
                    raw_response=data,
                )
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 429:
                    retry_after = int(e.response.headers.get("Retry-After", "60"))
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"OpenRouter API error: {e.response.status_code}")
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                last_error = e
                await asyncio.sleep(2 ** attempt)

        raise last_error or Exception("Max retries exceeded for OpenRouter")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


class MockAdapter(AIAdapter):
    """
    Mock AI adapter for testing without API calls.
    Returns deterministic responses based on prompt content.
    """

    def __init__(self, config: AIConfig):
        super().__init__(config)
        self.call_count = 0

    async def analyze(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> AIResponse:
        """Return mock response for testing."""
        self.call_count += 1

        # Deterministic response based on prompt keywords
        if "bullish" in prompt.lower() or "buy" in prompt.lower():
            thesis = "Mock bullish thesis based on positive economic indicators"
        elif "bearish" in prompt.lower() or "sell" in prompt.lower():
            thesis = "Mock bearish thesis based on negative economic indicators"
        else:
            thesis = "Mock neutral thesis - mixed economic signals"

        return AIResponse(
            content=json.dumps({
                "thesis": thesis,
                "sources": ["mock_source_1", "mock_source_2"],
                "causal_chain": [
                    {"step": 1, "description": "Mock economic event", "evidence": "mock", "confidence": "likely"},
                    {"step": 2, "description": "Mock market impact", "evidence": "mock", "confidence": "speculative"},
                ],
                "per_instrument_impact": [
                    {
                        "symbol": "XAUUSD",
                        "direction": "bullish",
                        "magnitude": "moderate",
                        "reasoning": "Mock safe-haven demand",
                        "key_levels": [],
                    },
                    {
                        "symbol": "EURUSD",
                        "direction": "neutral",
                        "magnitude": "weak",
                        "reasoning": "Mock mixed signals",
                        "key_levels": [],
                    },
                ],
                "counter_arguments": ["Mock counter-argument 1"],
                "invalidation_conditions": ["Mock invalidation condition"],
                "uncertainty_assessment": "Moderate uncertainty",
            }),
            model=self.config.model,
            provider="mock",
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            raw_response={"mock": True},
        )

    async def close(self) -> None:
        pass


def create_ai_adapter(
    config: AIConfig,
    api_key: Optional[str] = None,
    mock: bool = False,
) -> AIAdapter:
    """Factory function to create AI adapter."""
    if mock:
        return MockAdapter(config)

    if config.provider == "openrouter":
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY required for OpenRouter provider")
        return OpenRouterAdapter(config, api_key)

    raise ValueError(f"Unknown AI provider: {config.provider}")