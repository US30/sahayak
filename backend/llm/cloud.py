"""
CloudLLM — wraps DeepSeek API via the OpenAI-compatible SDK.

DeepSeek base URL: https://api.deepseek.com
Default model: deepseek-chat (DeepSeek-V3)
Reasoning model: deepseek-reasoner (DeepSeek-R1) — set MODEL_CLOUD in .env
"""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI

log = logging.getLogger(__name__)

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class CloudLLM:
    """Async wrapper around the DeepSeek chat completions API."""

    def __init__(self, config: Any) -> None:
        self._client = AsyncOpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=_DEEPSEEK_BASE_URL,
        )
        self.model: str = getattr(config, "MODEL_CLOUD", "deepseek-chat")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """Send a chat request and return the assistant text."""
        full_messages = [{"role": "system", "content": system}, *messages]
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def complete_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
    ) -> dict:
        """Send a request with tool definitions and return the full response dict."""
        full_messages = [{"role": "system", "content": system}, *messages]
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        return {
            "id": response.id,
            "model": response.model,
            "stop_reason": choice.finish_reason,
            "content": choice.message.content or "",
            "tool_calls": [
                tc.model_dump() for tc in (choice.message.tool_calls or [])
            ],
            "usage": {
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        }

    async def judge(
        self,
        query: str,
        response: str,
        criteria: list[str],
    ) -> dict:
        """LLM-as-judge scoring. Returns {"score": float, "reasoning": str, "criteria_scores": dict}."""
        import json

        criteria_list = "\n".join(f"- {c}" for c in criteria)
        judge_system = (
            "You are an impartial evaluator. "
            "Given a user query and a system response, score the response "
            "against the listed criteria. "
            "Return ONLY valid JSON with keys: "
            '"score" (float 0-1 overall), '
            '"reasoning" (str), '
            '"criteria_scores" (dict mapping each criterion to a float 0-1).'
        )
        judge_prompt = (
            f"Query: {query}\n\n"
            f"Response: {response}\n\n"
            f"Criteria:\n{criteria_list}"
        )
        raw = await self.complete(
            system=judge_system,
            messages=[{"role": "user", "content": judge_prompt}],
            max_tokens=512,
            temperature=0.0,
        )
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = "\n".join(clean.split("\n")[:-1])
            data = json.loads(clean)
            return {
                "score": float(data.get("score", 0.0)),
                "reasoning": str(data.get("reasoning", "")),
                "criteria_scores": {
                    str(k): float(v)
                    for k, v in data.get("criteria_scores", {}).items()
                },
            }
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning("judge: failed to parse LLM output – %s", exc)
            return {"score": 0.0, "reasoning": raw, "criteria_scores": {}}
