"""
Perceiver node — first step in the Sahayak agent graph.

Responsibilities
----------------
1. Optionally call the face-recognition HTTP service when an image is
   attached to the query.
2. Extract named entities (people, locations, times) from the query text
   via CloudLLM.
3. Resolve relative time references ("this morning", "yesterday") to
   concrete datetime ranges.
4. Classify query intent into one of five categories.
5. Write extracted metadata back into ``state["context"]``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from schemas import AgentState

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for entity / intent extraction
# ---------------------------------------------------------------------------

_ENTITY_SYSTEM = """You are a precise entity and intent extractor for a dementia-care memory assistant.

Given a user query, return ONLY a JSON object with these keys:
- "entities": {
    "people": [list of person names mentioned],
    "locations": [list of locations mentioned],
    "time_references": [list of raw time strings, e.g. "this morning", "yesterday", "at 3pm"]
  }
- "intent": one of "recall_person" | "recall_event" | "medication_check" | "routine_check" | "general"

Rules:
- intent = "recall_person" when asking about who someone is or who visited
- intent = "recall_event" when asking about a past activity or occurrence
- intent = "medication_check" when asking about medications, pills, doses
- intent = "routine_check" when asking about daily habits or scheduled activities
- intent = "general" for everything else
- Return ONLY the JSON object, no markdown fences, no extra text."""


# ---------------------------------------------------------------------------
# Time-reference resolver
# ---------------------------------------------------------------------------

_TIME_WINDOW_HOURS: dict[str, tuple[int, int]] = {
    "this morning": (6, 12),
    "morning": (6, 12),
    "afternoon": (12, 17),
    "this afternoon": (12, 17),
    "evening": (17, 21),
    "this evening": (17, 21),
    "night": (21, 24),
    "last night": (21, 24),
    "today": (0, 24),
}


def _resolve_time_references(
    refs: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Convert natural-language time references to a time range dict.

    Returns
    -------
    dict with keys "start", "end" (ISO strings) or empty dict.
    """
    if not refs:
        return {}

    if now is None:
        now = datetime.now()

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for ref in refs:
        ref_l = ref.strip().lower()

        # "yesterday"
        if "yesterday" in ref_l:
            start = today - timedelta(days=1)
            end = today
            return {"start": start.isoformat(), "end": end.isoformat()}

        # Named windows today
        for key, (h_start, h_end) in _TIME_WINDOW_HOURS.items():
            if key in ref_l:
                start = today + timedelta(hours=h_start)
                end = today + timedelta(hours=h_end)
                return {"start": start.isoformat(), "end": end.isoformat()}

        # "last N hours"
        m = re.search(r"last\s+(\d+)\s+hour", ref_l)
        if m:
            hours = int(m.group(1))
            start = now - timedelta(hours=hours)
            return {"start": start.isoformat(), "end": now.isoformat()}

        # "N hours ago"
        m = re.search(r"(\d+)\s+hour.*ago", ref_l)
        if m:
            hours = int(m.group(1))
            point = now - timedelta(hours=hours)
            start = point - timedelta(minutes=30)
            end = point + timedelta(minutes=30)
            return {"start": start.isoformat(), "end": end.isoformat()}

    # Fallback: today
    return {"start": today.isoformat(), "end": now.isoformat()}


# ---------------------------------------------------------------------------
# Face recognition helper
# ---------------------------------------------------------------------------

async def _call_face_service(image_b64: str, base_url: str) -> list[dict]:
    """POST the image to the local face recognition service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{base_url}/face/recognize",
                json={"image_b64": image_b64},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("people", [])
    except Exception as exc:
        log.warning("perceiver: face service error – %s", exc)
        return []


# ---------------------------------------------------------------------------
# Node implementation
# ---------------------------------------------------------------------------

async def perceiver_node(state: AgentState, config: dict) -> AgentState:
    """
    Analyse the query (and optional image) to extract structured metadata.

    Config keys used
    ----------------
    cloud_llm : CloudLLM
    face_service_url : str   (default "http://localhost:8001")
    """
    cloud_llm = config["cloud_llm"]
    face_service_url: str = config.get("face_service_url", "http://localhost:8001")

    query: str = state.get("query", "")
    image_b64: str | None = state.get("image_b64")

    context: dict = dict(state.get("context", {}))
    identified_people: list[dict] = list(state.get("identified_people", []))

    # 1. Face recognition if image present
    if image_b64:
        people_from_image = await _call_face_service(image_b64, face_service_url)
        identified_people.extend(people_from_image)

    # 2. Entity + intent extraction via LLM
    try:
        raw = await cloud_llm.complete(
            system=_ENTITY_SYSTEM,
            messages=[{"role": "user", "content": query}],
            max_tokens=256,
            temperature=0.0,
        )
        # Strip markdown fences if the model added them
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
        if clean.endswith("```"):
            clean = "\n".join(clean.split("\n")[:-1])
        extracted: dict = json.loads(clean)
    except (json.JSONDecodeError, Exception) as exc:
        log.warning("perceiver: entity extraction failed – %s", exc)
        extracted = {
            "entities": {"people": [], "locations": [], "time_references": []},
            "intent": "general",
        }

    entities: dict = extracted.get("entities", {})
    intent: str = extracted.get("intent", "general")
    time_refs: list[str] = entities.get("time_references", [])
    time_range: dict = _resolve_time_references(time_refs)

    context["entities"] = entities
    context["intent"] = intent
    context["time_range"] = time_range

    log.info(
        "perceiver: intent=%s people=%s time_range=%s",
        intent,
        entities.get("people", []),
        time_range,
    )

    return {
        **state,
        "context": context,
        "identified_people": identified_people,
    }
