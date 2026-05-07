"""
OCR module for Sahayak.

Extracts text from images using pytesseract + PIL, classifies the document
type via keyword heuristics, and parses medicine labels with regex (LLM
fallback when the regex yields insufficient structure).
"""
from __future__ import annotations

import asyncio
import io
import re
from typing import Any

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image

from config import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Keyword sets for document classification
# ---------------------------------------------------------------------------
_MEDICINE_KEYWORDS: frozenset[str] = frozenset(
    {
        "mg", "mcg", "ml", "tablet", "tablets", "capsule", "capsules",
        "dose", "dosage", "syrup", "injection", "ointment", "drops",
        "prescription", "rx", "refill", "dispense", "pharmacy",
        "morning", "evening", "night", "twice", "thrice", "daily",
        "before meal", "after meal", "empty stomach",
    }
)
_SIGN_KEYWORDS: frozenset[str] = frozenset(
    {
        "exit", "entrance", "danger", "warning", "stop", "caution",
        "no entry", "do not", "restricted", "emergency", "hospital",
        "toilet", "restroom", "parking",
    }
)
_DOCUMENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "dear", "sincerely", "regards", "invoice", "receipt", "bill",
        "date", "signature", "hereby", "whereas", "agreement",
        "certificate", "report", "discharge", "diagnosis",
    }
)

# ---------------------------------------------------------------------------
# Regex patterns for medicine label parsing
# ---------------------------------------------------------------------------
_MED_NAME_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9\-\s]+?)(?:\s+\d|\s+tablet|\s+capsule|\s+syrup|$)",
    re.IGNORECASE | re.MULTILINE,
)
_DOSAGE_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:mg|mcg|ml|g|iu|units?))",
    re.IGNORECASE,
)
_FREQ_RE = re.compile(
    r"(once\s+daily|twice\s+daily|thrice\s+daily|"
    r"(?:1|2|3|4)\s+times?\s+(?:a\s+day|daily|per\s+day)|"
    r"every\s+\d+\s+hours?|"
    r"(?:morning|evening|night|bedtime)(?:\s+and\s+(?:morning|evening|night|bedtime))*)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class OCRService:
    """
    OCR service backed by pytesseract + PIL.

    Provides text extraction, document-type classification, and structured
    medicine-label parsing.  The LLM fallback for ``parse_medicine_label``
    is activated when the regex extracts fewer than two of the three key
    fields (name, dosage, frequency).
    """

    def __init__(self) -> None:
        self._ready = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        log.info("ocr.init.start")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._check_tesseract)
        self._ready = True
        log.info("ocr.init.done")

    def _check_tesseract(self) -> None:
        import pytesseract  # type: ignore[import]

        pytesseract.get_tesseract_version()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_type(self, text: str) -> str:
        """Classify document type from extracted text using keyword heuristics."""
        lower = text.lower()
        tokens = set(re.split(r"\W+", lower))

        medicine_score = len(tokens & _MEDICINE_KEYWORDS)
        if medicine_score >= 2:
            return "medicine_label"

        sign_score = sum(1 for kw in _SIGN_KEYWORDS if kw in lower)
        if sign_score >= 1 and len(text) < 200:
            return "sign"

        doc_score = len(tokens & _DOCUMENT_KEYWORDS)
        if doc_score >= 2:
            return "document"

        return "unknown"

    def _run_ocr_sync(self, image_bytes: bytes) -> str:
        import pytesseract  # type: ignore[import]

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        # Use both English and Hindi data if available; fall back gracefully
        try:
            text: str = pytesseract.image_to_string(img, lang="eng+hin")
        except Exception:
            text = pytesseract.image_to_string(img, lang="eng")
        return text.strip()

    def _parse_medicine_label_sync(self, text: str) -> dict[str, Any]:
        """
        Extract medicine name, dosage, and frequency from *text* using regex.

        Falls back to an Anthropic LLM call when the regex yields fewer than
        two of the three target fields, ensuring robust parsing even for
        non-standard label layouts.
        """
        # Regex extraction
        name_match = _MED_NAME_RE.search(text)
        med_name: str = name_match.group(1).strip() if name_match else ""

        dosage_matches = _DOSAGE_RE.findall(text)
        dosage: str = dosage_matches[0] if dosage_matches else ""

        freq_match = _FREQ_RE.search(text)
        frequency: str = freq_match.group(0).strip() if freq_match else ""

        # Count how many fields were successfully extracted
        extracted_count = sum(bool(x) for x in [med_name, dosage, frequency])

        if extracted_count < 2:
            # LLM fallback: call Anthropic to parse the label
            llm_result = self._llm_parse_sync(text)
            if llm_result:
                med_name = med_name or llm_result.get("name", "")
                dosage = dosage or llm_result.get("dosage", "")
                frequency = frequency or llm_result.get("frequency", "")

        return {
            "name": med_name,
            "dosage": dosage,
            "frequency": frequency,
        }

    def _llm_parse_sync(self, text: str) -> dict[str, Any]:
        """Call DeepSeek to extract medicine fields from raw OCR text."""
        try:
            import json as _json

            from openai import OpenAI

            client = OpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
            )
            prompt = (
                "Extract medicine information from the following OCR text. "
                "Return ONLY a JSON object with keys: name, dosage, frequency. "
                "Use empty string if a field cannot be determined.\n\n"
                f"OCR Text:\n{text}\n\nJSON:"
            )
            response = client.chat.completions.create(
                model=settings.MODEL_CLOUD,
                max_tokens=256,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": "You are a precise medical data extractor. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
            )
            raw = (response.choices[0].message.content or "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return _json.loads(raw)
        except Exception as exc:
            log.warning("ocr.llm_parse.error", error=str(exc))
            return {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract_text(self, image_bytes: bytes) -> dict[str, Any]:
        """
        Extract text from *image_bytes*, classify its type, and (for medicine
        labels) parse structured fields.

        Returns::

            {
                "text": str,
                "type": "medicine_label" | "sign" | "document" | "unknown",
                "parsed": dict,   # populated for medicine_label, else {}
            }
        """
        loop = asyncio.get_running_loop()
        text: str = await loop.run_in_executor(
            None, self._run_ocr_sync, image_bytes
        )
        doc_type = self._classify_type(text)

        parsed: dict[str, Any] = {}
        if doc_type == "medicine_label":
            parsed = await self.parse_medicine_label(text)

        return {"text": text, "type": doc_type, "parsed": parsed}

    async def parse_medicine_label(self, text: str) -> dict[str, Any]:
        """
        Parse *text* (from a medicine label) to extract name, dosage, and
        frequency.  Uses regex with an Anthropic LLM fallback.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._parse_medicine_label_sync, text
        )


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["ocr"])

_service: OCRService | None = None
_init_lock = asyncio.Lock()


async def _get_service() -> OCRService:
    global _service
    if _service is None:
        async with _init_lock:
            if _service is None:
                svc = OCRService()
                await svc.initialize()
                _service = svc
    return _service


@router.post("/ocr")
async def api_ocr(
    image_file: UploadFile = File(...),
) -> dict[str, Any]:
    """
    Extract and classify text from an uploaded image.

    Returns ``{"text": str, "type": str, "parsed": dict}`` where *type* is
    one of ``medicine_label``, ``sign``, ``document``, or ``unknown``.
    """
    svc = await _get_service()
    image_bytes = await image_file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is empty.")
    return await svc.extract_text(image_bytes)
