"""
Planner node — reviews retrieved context and decides how to answer.

Responsibilities
----------------
1. Inspect retrieved_memories and intent to judge whether enough context
   exists for a direct answer.
2. Produce an ordered plan (list of step strings) that the speaker will
   follow.
3. Call the router's complexity scorer to set routing_decision and
   confidence on the state.
"""

from __future__ import annotations

import logging

from schemas import AgentState

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plan templates by intent
# ---------------------------------------------------------------------------

_INTENT_PLANS: dict[str, list[str]] = {
    "recall_person": [
        "identify_person_from_memories",
        "describe_context_of_meeting",
        "compose_answer",
    ],
    "recall_event": [
        "find_event_in_memories",
        "determine_time_and_place",
        "compose_answer",
    ],
    "medication_check": [
        "check_med_log",
        "check_routine",
        "compose_answer",
    ],
    "routine_check": [
        "check_routine",
        "compare_with_schedule",
        "compose_answer",
    ],
    "general": [
        "search_relevant_memories",
        "compose_answer",
    ],
}

_INSUFFICIENT_PLAN: list[str] = [
    "insufficient_context",
    "use_semantic_profile",
    "compose_gentle_apology",
]


async def planner_node(state: AgentState, config: dict) -> AgentState:
    """
    Decide the response plan and routing.

    Config keys used
    ----------------
    router : EdgeCloudRouter   (has .score_complexity() method)
    """
    router = config.get("router")

    retrieved_memories: list[dict] = state.get("retrieved_memories", [])
    context: dict = state.get("context", {})
    intent: str = context.get("intent", "general")
    query: str = state.get("query", "")

    # ------------------------------------------------------------------
    # 1. Judge whether we have enough context for a direct answer
    # ------------------------------------------------------------------
    has_memories = len(retrieved_memories) > 0

    if not has_memories:
        plan = _INSUFFICIENT_PLAN.copy()
        # Low confidence — we can't anchor the answer in memory
        confidence: float = 0.1
    else:
        plan = _INTENT_PLANS.get(intent, _INTENT_PLANS["general"]).copy()

        # Richer plans warrant higher confidence
        if intent == "medication_check":
            # Check whether at least one memory has a "medication" tag
            med_tags = any(
                "medication" in (m.get("tags") or []) for m in retrieved_memories
            )
            confidence = 0.85 if med_tags else 0.55
        elif len(retrieved_memories) >= 3:
            confidence = 0.80
        else:
            confidence = 0.60

    # ------------------------------------------------------------------
    # 2. Routing decision
    # ------------------------------------------------------------------
    routing_decision: str = "cloud"

    if router is not None:
        try:
            # Use the router's complexity scoring heuristic
            score: float = router.score_complexity(query)
            on_device_available = (
                hasattr(router, "_on_device_model")
                and router._on_device_model is not None
            )
            from config import settings

            threshold: float = settings.ON_DEVICE_THRESHOLD
            routing_decision = (
                "on_device"
                if on_device_available and score < threshold
                else "cloud"
            )
        except Exception as exc:
            log.warning("planner: routing decision failed – %s", exc)
            routing_decision = "cloud"

    log.info(
        "planner: intent=%s plan=%s confidence=%.2f routing=%s",
        intent,
        plan,
        confidence,
        routing_decision,
    )

    return {
        **state,
        "plan": plan,
        "confidence": confidence,
        "routing_decision": routing_decision,
    }
