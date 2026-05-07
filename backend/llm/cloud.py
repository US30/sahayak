"""
CloudLLM — wraps Anthropic's AsyncAnthropic client.

Prompt caching: system prompts longer than 1000 tokens are automatically
sent with cache_control=ephemeral so repeated calls within 5 minutes skip
full tokenisation cost.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

log = logging.getLogger(__name__)


class CloudLLM:
    """Async wrapper around the Anthropic messages API."""

    def __init__(self, config: Any) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=config.ANTHROPIC_API_KEY,
        )
        self.model: str = getattr(config, "MODEL_CLOUD", "claude-opus-4-7")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_system(self, system: str) -> list[dict] | str:
        """Return the system param, adding cache_control when prompt is long."""
        if len(system) > 1000:
            return [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        return system

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
        """
        Send a chat request and return the assistant text.

        Parameters
        ----------
        system:
            System prompt string.
        messages:
            List of ``{"role": ..., "content": ...}`` dicts.
        max_tokens:
            Upper bound on output tokens.
        temperature:
            Sampling temperature (0-1).

        Returns
        -------
        str
            First text block in the response.
        """
        response = await self._client.messages.create(
            model=self.model,
            system=self._build_system(system),  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
        )
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""

    async def complete_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
    ) -> dict:
        """
        Send a request with tool definitions and return the full response.

        Returns the raw response as a dict so callers can inspect
        ``stop_reason``, ``content``, and individual tool-use blocks.
        """
        response = await self._client.messages.create(
            model=self.model,
            system=self._build_system(system),  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            max_tokens=max_tokens,
        )
        return {
            "id": response.id,
            "model": response.model,
            "stop_reason": response.stop_reason,
            "content": [
                block.model_dump() if hasattr(block, "model_dump") else dict(block)
                for block in response.content
            ],
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": getattr(
                    response.usage, "cache_read_input_tokens", 0
                ),
                "cache_creation_input_tokens": getattr(
                    response.usage, "cache_creation_input_tokens", 0
                ),
            },
        }

    async def judge(
        self,
        query: str,
        response: str,
        criteria: list[str],
    ) -> dict:
        """
        LLM-as-judge scoring.

        Asks the model to rate ``response`` against each criterion in
        ``criteria`` on a 0–1 scale and return structured JSON.

        Returns
        -------
        dict
            ``{"score": float, "reasoning": str, "criteria_scores": dict}``
        """
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
            # Strip markdown fences if present
            clean = raw.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = "\n".join(clean.split("\n")[:-1])
            data = json.loads(clean)
            # Normalise
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
