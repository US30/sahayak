"""
Automatic speech recognition module for Sahayak.

Uses faster-whisper (CTranslate2 backend) with VAD filtering.
Supports Hindi, Tamil, Telugu, Bengali, English and code-mixed variants.
Language auto-detection is triggered when language="auto".
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from config import settings
from schemas import TranscriptionResult

log = structlog.get_logger(__name__)

# Languages explicitly supported (passed to Whisper's language parameter)
_SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {"hi", "ta", "te", "bn", "en", "auto"}
)


class ASRService:
    """
    Automatic speech recognition backed by faster-whisper.

    Model is loaded once during ``initialize()`` and reused for all requests.
    Transcription runs in a thread-pool executor so the event loop is never
    blocked.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        log.info("asr.init.start", model=settings.WHISPER_MODEL)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_model)
        log.info("asr.init.done")

    def _load_model(self) -> None:
        from faster_whisper import WhisperModel  # type: ignore[import]

        self._model = WhisperModel(
            settings.WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
        )

    # ------------------------------------------------------------------
    # Core transcription
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_bytes: bytes,
        language: str = "hi",
    ) -> TranscriptionResult:
        """
        Transcribe *audio_bytes* and return a ``TranscriptionResult``.

        Parameters
        ----------
        audio_bytes:
            Raw audio data in any ffmpeg-supported format (WAV, WebM, MP3, OGG …).
        language:
            ISO-639-1 language code.  Pass ``"auto"`` to let Whisper detect
            the language automatically.  Supported codes: ``hi``, ``ta``,
            ``te``, ``bn``, ``en``, ``auto``.
        """
        if language not in _SUPPORTED_LANGUAGES:
            log.warning("asr.unsupported_language", lang=language)
            # Fall back to auto-detect rather than hard-failing
            language = "auto"

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._transcribe_sync, audio_bytes, language
        )

    def _transcribe_sync(
        self, audio_bytes: bytes, language: str
    ) -> TranscriptionResult:
        """Blocking transcription — runs inside a thread-pool executor."""
        suffix = ".webm"  # works for WAV / WebM / OGG / MP3 when ffmpeg is present
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            whisper_lang = None if language == "auto" else language
            segments_iter, info = self._model.transcribe(  # type: ignore[union-attr]
                tmp_path,
                language=whisper_lang,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )

            segment_list: list[dict[str, Any]] = []
            full_parts: list[str] = []

            for seg in segments_iter:
                text = seg.text.strip()
                if text:
                    full_parts.append(text)
                    segment_list.append(
                        {
                            "start": round(seg.start, 3),
                            "end": round(seg.end, 3),
                            "text": text,
                            "avg_logprob": round(seg.avg_logprob, 4),
                        }
                    )

            full_text = " ".join(full_parts).strip()

            # Convert avg_logprob (typically -1.0 … 0.0) → confidence 0–1
            if segment_list:
                mean_logprob = sum(s["avg_logprob"] for s in segment_list) / len(
                    segment_list
                )
                confidence = float(min(1.0, max(0.0, 1.0 + mean_logprob)))
            else:
                confidence = 0.0

            detected_lang = info.language if language == "auto" else language

            return TranscriptionResult(
                text=full_text,
                confidence=confidence,
                language=detected_lang,
                segments=segment_list,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["asr"])

_service: ASRService | None = None
_init_lock = asyncio.Lock()


async def _get_service() -> ASRService:
    global _service
    if _service is None:
        async with _init_lock:
            if _service is None:
                svc = ASRService()
                await svc.initialize()
                _service = svc
    return _service


@router.post("/transcribe", response_model=TranscriptionResult)
async def api_transcribe(
    audio_file: UploadFile = File(..., description="Audio file (WAV, WebM, MP3, OGG)"),
    language: str = Form(default="auto", description="ISO-639-1 code or 'auto'"),
) -> TranscriptionResult:
    """
    Transcribe uploaded audio.

    Supports Hindi (``hi``), Tamil (``ta``), Telugu (``te``), Bengali (``bn``),
    English (``en``) and code-mixed variants.  Pass ``language=auto`` for
    automatic language detection.
    """
    svc = await _get_service()
    audio_bytes = await audio_file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")
    return await svc.transcribe(audio_bytes, language)
