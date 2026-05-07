from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import structlog

from config import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Complexity heuristics
# ---------------------------------------------------------------------------

_COMPLEX_PATTERNS = re.compile(
    r"\b(diagnos|medic|emergency|hospital|remember\s+when|who\s+is|legal|suicide|"
    r"danger|lost|help\s+me|confused|don'?t\s+know|forget|forgot)\b",
    re.IGNORECASE,
)

_LENGTH_WEIGHT = 0.30
_PATTERN_WEIGHT = 0.50
_VOCAB_WEIGHT = 0.20


def _compute_complexity(query: str) -> float:
    """Return a [0, 1] complexity score. Higher → route to cloud."""
    words = query.split()
    word_count = len(words)

    length_score = min(1.0, word_count / 50.0)
    pattern_score = min(1.0, len(_COMPLEX_PATTERNS.findall(query)) * 0.25)
    avg_word_len = (sum(len(w) for w in words) / word_count) if words else 0.0
    vocab_score = min(1.0, avg_word_len / 8.0)

    return (
        _LENGTH_WEIGHT * length_score
        + _PATTERN_WEIGHT * pattern_score
        + _VOCAB_WEIGHT * vocab_score
    )


# ---------------------------------------------------------------------------
# EdgeCloudRouter
# ---------------------------------------------------------------------------


class EdgeCloudRouter:
    """Routes LLM inference to on-device (llama-cpp) or cloud (DeepSeek)
    based on a lightweight query-complexity heuristic."""

    def __init__(self) -> None:
        self._on_device_model: Any = None
        self._cloud_client: Any = None

    async def initialize(self) -> None:
        log.info(
            "router.init.start",
            threshold=settings.ON_DEVICE_THRESHOLD,
            cloud_model=settings.MODEL_CLOUD,
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_clients)
        log.info("router.init.done")

    def _load_clients(self) -> None:
        from openai import AsyncOpenAI

        self._cloud_client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )

        model_path = settings.MODEL_ON_DEVICE
        if os.path.exists(model_path):
            from llama_cpp import Llama

            self._on_device_model = Llama(
                model_path=model_path,
                n_ctx=2048,
                n_threads=4,
                verbose=False,
            )
            log.info("router.on_device_model.loaded", path=model_path)
        else:
            log.warning(
                "router.on_device_model.not_found",
                path=model_path,
                note="All queries will fall back to cloud model",
            )

    async def route(
        self,
        query: str,
        system_prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, str, float]:
        """Generate a response, choosing edge or cloud automatically.

        Returns:
            (response_text, routing_decision, complexity_score)
            routing_decision is ``"on_device"`` or ``"cloud"``.
        """
        history = history or []
        complexity = _compute_complexity(query)
        use_cloud = (
            complexity >= settings.ON_DEVICE_THRESHOLD
            or self._on_device_model is None
        )

        if use_cloud:
            text = await self._call_cloud(query, system_prompt, history)
            return text, "cloud", complexity

        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(
            None, self._call_on_device, query, system_prompt, history
        )
        return text, "on_device", complexity

    # ------------------------------------------------------------------
    # Cloud inference
    # ------------------------------------------------------------------

    async def _call_cloud(
        self,
        query: str,
        system_prompt: str,
        history: list[dict[str, str]],
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": query},
        ]
        response = await self._cloud_client.chat.completions.create(
            model=settings.MODEL_CLOUD,
            max_tokens=1024,
            messages=messages,  # type: ignore[arg-type]
        )
        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # On-device inference
    # ------------------------------------------------------------------

    def _call_on_device(
        self,
        query: str,
        system_prompt: str,
        history: list[dict[str, str]],
    ) -> str:
        prompt_parts = [f"<system>{system_prompt}</system>"]
        for msg in history:
            role = msg.get("role", "user")
            prompt_parts.append(f"<{role}>{msg['content']}</{role}>")
        prompt_parts.append(f"<user>{query}</user><assistant>")

        output = self._on_device_model(
            "\n".join(prompt_parts),
            max_tokens=512,
            stop=["</assistant>", "<user>"],
            echo=False,
        )
        return output["choices"][0]["text"].strip()
