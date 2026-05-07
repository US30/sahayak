"""
Speaker node — final step that generates the compassionate response.

Responsibilities
----------------
1. Select the right LLM backend based on routing_decision.
2. Build a context-rich prompt from retrieved memories and the plan.
3. Generate a warm, concise answer in the patient's language.
4. Populate ``state["response"]``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from schemas import AgentState

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt — Sahayak persona
# ---------------------------------------------------------------------------

_SAHAYAK_SYSTEM = """You are Sahayak, a warm and patient memory assistant for a person with mild cognitive impairment.

Guidelines:
- Answer in simple, reassuring language.
- Use the person's language — mix Hindi and English naturally if that feels right (Hinglish is fine).
- Keep responses under 3 sentences.
- Never express uncertainty harshly — always be gentle and supportive.
- If you have memory details, weave them naturally into the answer.
- If information is missing, offer gentle reassurance rather than alarming the person.
- Do not start with "I" — vary your openers.
- Avoid clinical or complex vocabulary."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_memories(memories: list[dict]) -> str:
    """Render retrieved memories as a concise context block."""
    if not memories:
        return "(No specific memories found for this query.)"
    lines: list[str] = []
    for i, mem in enumerate(memories[:5], 1):
        ts = mem.get("timestamp", "")
        if isinstance(ts, datetime):
            ts = ts.strftime("%Y-%m-%d %H:%M")
        text = mem.get("text", "")
        people = ", ".join(mem.get("people", []))
        loc = ""
        if isinstance(mem.get("location"), dict):
            loc_d: dict = mem["location"]
            loc = loc_d.get("name", "")
        parts = [f"[{i}] {text}"]
        if ts:
            parts.append(f"(time: {ts})")
        if people:
            parts.append(f"(people: {people})")
        if loc:
            parts.append(f"(place: {loc})")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _format_people(people: list[dict]) -> str:
    """Render identified people as a concise context block."""
    if not people:
        return ""
    parts: list[str] = []
    for p in people:
        name = p.get("name", "unknown person")
        rel = p.get("relationship", "")
        entry = name
        if rel:
            entry += f" ({rel})"
        parts.append(entry)
    return "People recognised in image: " + ", ".join(parts)


def _build_user_message(state: AgentState) -> str:
    """Construct the full context message for the LLM."""
    query = state.get("query", "")
    plan = state.get("plan", [])
    retrieved_memories = state.get("retrieved_memories", [])
    identified_people = state.get("identified_people", [])
    context: dict = state.get("context", {})
    intent = context.get("intent", "general")

    memory_block = _format_memories(retrieved_memories)
    people_block = _format_people(identified_people)

    parts = [
        f"User query: {query}",
        f"Intent detected: {intent}",
        f"Response plan: {', '.join(plan)}",
        "",
        "=== Memory context ===",
        memory_block,
    ]
    if people_block:
        parts.append("")
        parts.append(people_block)

    parts += [
        "",
        "Please answer the query using the memory context above.",
        "Be warm, brief (≤3 sentences), and reassuring.",
    ]

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Insufficient-context template
# ---------------------------------------------------------------------------

_FALLBACK_RESPONSE = (
    "Mujhe abhi yeh yaad nahi aa raha, lekin koi baat nahi. "
    "Aap apne caregiver se pooch sakte hain, ya thodi der mein dobara try karein."
)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

async def speaker_node(state: AgentState, config: dict) -> AgentState:
    """
    Generate the final user-facing response.

    Config keys used
    ----------------
    cloud_llm    : CloudLLM
    on_device_llm : OnDeviceLLM   (optional)
    """
    plan: list[str] = state.get("plan", [])
    routing_decision: str = state.get("routing_decision", "cloud")
    confidence: float = state.get("confidence", 0.5)

    # Low-confidence short-circuit
    if confidence < 0.2 or "insufficient_context" in plan:
        log.info("speaker: insufficient context, using fallback response")
        return {**state, "response": _FALLBACK_RESPONSE}

    cloud_llm = config.get("cloud_llm")
    on_device_llm = config.get("on_device_llm")

    user_message = _build_user_message(state)
    messages = [{"role": "user", "content": user_message}]

    response_text: str = ""

    # Try on-device if routing says so
    if routing_decision == "on_device" and on_device_llm is not None:
        try:
            if on_device_llm.is_available():
                full_prompt = f"{_SAHAYAK_SYSTEM}\n\n{user_message}"
                response_text = await on_device_llm.complete(
                    full_prompt, max_tokens=256, temperature=0.6
                )
                log.info("speaker: responded via on_device model")
        except Exception as exc:
            log.warning("speaker: on_device failed (%s), falling back to cloud", exc)
            response_text = ""

    # Fall back (or primary cloud path)
    if not response_text and cloud_llm is not None:
        try:
            response_text = await cloud_llm.complete(
                system=_SAHAYAK_SYSTEM,
                messages=messages,
                max_tokens=256,
                temperature=0.6,
            )
            log.info("speaker: responded via cloud model")
        except Exception as exc:
            log.error("speaker: cloud LLM failed – %s", exc)
            response_text = _FALLBACK_RESPONSE

    if not response_text:
        response_text = _FALLBACK_RESPONSE

    return {**state, "response": response_text}
