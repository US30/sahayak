"""
Text-to-speech module for Sahayak.

Primary engine: Coqui XTTS v2 (multilingual, voice-cloning capable).
Fallback engine: pyttsx3 (offline, no GPU required) when Coqui is unavailable.

Supports Hindi, Tamil, Telugu, Bengali, English and code-mixed variants.
"""
from __future__ import annotations

import asyncio
import io
import tempfile
import wave
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from config import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Language code normalisation
# ---------------------------------------------------------------------------
# Coqui XTTS v2 uses full language names; map ISO codes to what XTTS expects.
_LANG_MAP: dict[str, str] = {
    "hi": "hi",   # Hindi
    "ta": "ta",   # Tamil
    "te": "te",   # Telugu
    "bn": "bn",   # Bengali
    "en": "en",   # English
    "mr": "mr",   # Marathi (bonus — XTTS supports it)
    "gu": "gu",   # Gujarati
}
_DEFAULT_LANG = "hi"


def _normalise_lang(language: str) -> str:
    code = language.lower().strip()
    return _LANG_MAP.get(code, _DEFAULT_LANG)


# ---------------------------------------------------------------------------
# Coqui XTTS v2 engine
# ---------------------------------------------------------------------------

class _CoquiEngine:
    """Thin wrapper around Coqui TTS XTTS v2."""

    def __init__(self) -> None:
        self._tts: Any = None

    def load(self) -> None:
        from TTS.api import TTS  # type: ignore[import]

        self._tts = TTS(model_name=settings.TTS_MODEL, progress_bar=False)
        log.info("tts.coqui.loaded", model=settings.TTS_MODEL)

    def synthesize(
        self,
        text: str,
        language: str,
        speaker_wav: str | None,
    ) -> bytes:
        """Synthesize *text* to WAV bytes.  *speaker_wav* is a file path."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
            out_path = out_f.name
        try:
            self._tts.tts_to_file(  # type: ignore[union-attr]
                text=text,
                file_path=out_path,
                language=language,
                speaker_wav=speaker_wav,  # None → use default speaker
            )
            return Path(out_path).read_bytes()
        finally:
            Path(out_path).unlink(missing_ok=True)

    @property
    def available(self) -> bool:
        return self._tts is not None


# ---------------------------------------------------------------------------
# pyttsx3 fallback engine
# ---------------------------------------------------------------------------

class _Pyttsx3Engine:
    """Minimal pyttsx3-backed TTS.  Produces WAV audio synchronously."""

    def synthesize(self, text: str) -> bytes:
        import pyttsx3  # type: ignore[import]

        engine = pyttsx3.init()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name
        try:
            engine.save_to_file(text, out_path)
            engine.runAndWait()
            return Path(out_path).read_bytes()
        finally:
            engine.stop()
            Path(out_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class TTSService:
    """
    Multilingual TTS service.

    Uses Coqui XTTS v2 as the primary engine.  If Coqui is unavailable at
    startup (e.g. missing GPU drivers or model files), falls back to pyttsx3
    so the service remains functional in constrained environments.

    Call ``await instance.initialize()`` before first use.
    """

    def __init__(self) -> None:
        self._coqui = _CoquiEngine()
        self._pyttsx3 = _Pyttsx3Engine()
        self._use_coqui = False
        # speaker_id → temp WAV path on disk (populated from speaker registry)
        self._speaker_paths: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        log.info("tts.init.start")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_engines)
        log.info(
            "tts.init.done",
            engine="coqui" if self._use_coqui else "pyttsx3",
        )

    def _load_engines(self) -> None:
        try:
            self._coqui.load()
            self._use_coqui = True
        except Exception as exc:
            log.warning("tts.coqui.unavailable", error=str(exc))
            self._use_coqui = False
            # Validate that pyttsx3 is importable
            try:
                import pyttsx3  # type: ignore[import]  # noqa: F401
            except ImportError:
                log.error("tts.pyttsx3.unavailable")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        language: str = "hi",
        speaker_wav: str | None = None,
    ) -> bytes:
        """
        Generate speech for *text* and return WAV bytes.

        Parameters
        ----------
        text:
            Text to synthesise.  Code-mixed text (e.g. Hinglish) is accepted.
        language:
            ISO-639-1 language code (``hi``, ``ta``, ``te``, ``bn``, ``en``).
        speaker_wav:
            Path to a reference WAV file (≥ 6 s) for voice cloning.  When
            provided, XTTS v2 clones the voice of the reference speaker.
            Ignored when the fallback engine is in use.
        """
        lang = _normalise_lang(language)
        loop = asyncio.get_running_loop()

        if self._use_coqui:
            wav_bytes: bytes = await loop.run_in_executor(
                None, self._coqui.synthesize, text, lang, speaker_wav
            )
        else:
            wav_bytes = await loop.run_in_executor(
                None, self._pyttsx3.synthesize, text
            )

        return wav_bytes

    def register_speaker_wav(self, speaker_id: str, wav_path: str) -> None:
        """
        Associate a *speaker_id* with a reference WAV path for voice cloning.

        This allows the HTTP route to accept a ``speaker_id`` string rather
        than requiring callers to upload the reference file on every request.
        """
        self._speaker_paths[speaker_id] = wav_path
        log.info("tts.speaker.registered", speaker_id=speaker_id)

    def speaker_wav_path(self, speaker_id: str) -> str | None:
        return self._speaker_paths.get(speaker_id)


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["tts"])

_service: TTSService | None = None
_init_lock = asyncio.Lock()


async def _get_service() -> TTSService:
    global _service
    if _service is None:
        async with _init_lock:
            if _service is None:
                svc = TTSService()
                await svc.initialize()
                _service = svc
    return _service


class _TTSRequest(BaseModel):
    text: str
    language: str = "hi"
    speaker_id: str | None = None


@router.post("/tts")
async def api_tts(body: _TTSRequest) -> Response:
    """
    Synthesise speech from text.

    Body fields:
    - ``text``: the text to speak
    - ``language``: ISO-639-1 code (default ``hi``)
    - ``speaker_id``: optional registered speaker id for voice cloning

    Returns ``audio/wav`` binary response.
    """
    svc = await _get_service()

    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Text must not be empty.")

    speaker_wav: str | None = None
    if body.speaker_id:
        speaker_wav = svc.speaker_wav_path(body.speaker_id)
        if speaker_wav is None:
            raise HTTPException(
                status_code=404,
                detail=f"Speaker id '{body.speaker_id}' is not registered.",
            )

    audio_bytes = await svc.synthesize(
        text=body.text,
        language=body.language,
        speaker_wav=speaker_wav,
    )
    return Response(content=audio_bytes, media_type="audio/wav")
