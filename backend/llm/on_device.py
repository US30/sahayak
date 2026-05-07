"""
OnDeviceLLM — wraps llama_cpp for fully local inference.

If the model file does not exist at startup the instance marks itself
unavailable and raises RuntimeError on any completion request rather
than crashing the whole process.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class OnDeviceLLM:
    """Local GGUF inference via llama-cpp-python."""

    def __init__(self, config: Any) -> None:
        model_path: str = getattr(
            config, "MODEL_ON_DEVICE", "models/gemma-2-2b-it/gemma-2-2b-it.Q4_K_M.gguf"
        )
        self.available: bool = False
        self._llm: Any = None

        if not Path(model_path).exists():
            log.warning(
                "OnDeviceLLM: model file not found at '%s'. "
                "On-device inference will be unavailable.",
                model_path,
            )
            return

        try:
            from llama_cpp import Llama  # type: ignore

            self._llm = Llama(
                model_path=model_path,
                n_ctx=2048,
                n_threads=4,
                verbose=False,
            )
            self.available = True
            log.info("OnDeviceLLM: loaded model from '%s'", model_path)
        except Exception as exc:  # pragma: no cover
            log.warning(
                "OnDeviceLLM: failed to load model – %s. "
                "On-device inference will be unavailable.",
                exc,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the model loaded successfully."""
        return self.available

    async def complete(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """
        Run inference asynchronously.

        Runs the blocking llama_cpp call in a thread pool so the
        asyncio event loop remains unblocked.

        Raises
        ------
        RuntimeError
            If the model is not loaded.
        """
        if not self.available or self._llm is None:
            raise RuntimeError(
                "On-device model not loaded. "
                "Check MODEL_ON_DEVICE path or install llama-cpp-python."
            )

        loop = asyncio.get_event_loop()
        call = partial(
            self._llm,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            echo=False,
        )
        result: dict = await loop.run_in_executor(None, call)
        try:
            return result["choices"][0]["text"].strip()
        except (KeyError, IndexError) as exc:
            log.error("OnDeviceLLM: unexpected output structure – %s", exc)
            return ""
