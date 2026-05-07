"""
Recaller node — retrieves relevant episodic memories and semantic facts.

Responsibilities
----------------
1. Query EpisodicMemory with time-range and people filters from perceiver output.
2. For medication_check intent: also query today's medication log.
3. Populate ``state["retrieved_memories"]`` with up to 5 most-relevant chunks.
4. Merge face-recognised people from perceiver into ``state["identified_people"]``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from schemas import AgentState

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_range() -> tuple[str, str]:
    """Return ISO strings for the start and end of today."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    return today.isoformat(), tomorrow.isoformat()


def _chunk_to_dict(chunk: Any) -> dict:
    """Convert a MemoryChunk model (or plain dict) to a plain dict."""
    if isinstance(chunk, dict):
        return chunk
    if hasattr(chunk, "model_dump"):
        return chunk.model_dump()
    return dict(chunk)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

async def recaller_node(state: AgentState, config: dict) -> AgentState:
    """
    Retrieve memories and profile facts relevant to the current query.

    Config keys used
    ----------------
    episodic_memory  : EpisodicMemory   (from memory module)
    semantic_profile : SemanticProfile  (from memory module, optional)
    """
    episodic_memory = config.get("episodic_memory")
    semantic_profile = config.get("semantic_profile")

    context: dict = state.get("context", {})
    intent: str = context.get("intent", "general")
    entities: dict = context.get("entities", {})
    time_range: dict = context.get("time_range", {})
    user_id: str = state.get("user_id", "")
    query: str = state.get("query", "")

    retrieved_memories: list[dict] = []
    identified_people: list[dict] = list(state.get("identified_people", []))

    # ------------------------------------------------------------------
    # 1. Episodic memory query
    # ------------------------------------------------------------------
    if episodic_memory is not None:
        try:
            # Build filter kwargs for the memory store
            filter_kwargs: dict[str, Any] = {"user_id": user_id}

            if time_range:
                filter_kwargs["start_time"] = time_range.get("start")
                filter_kwargs["end_time"] = time_range.get("end")

            # Map person names to IDs when the memory store supports it
            people_names: list[str] = entities.get("people", [])
            if people_names:
                filter_kwargs["people_names"] = people_names

            raw_chunks = await episodic_memory.query(
                query_text=query,
                top_k=10,
                **filter_kwargs,
            )
            # Normalise and deduplicate by chunk id, keep top 5
            seen_ids: set[str] = set()
            for chunk in raw_chunks:
                d = _chunk_to_dict(chunk)
                cid = d.get("id", "")
                if cid and cid in seen_ids:
                    continue
                if cid:
                    seen_ids.add(cid)
                retrieved_memories.append(d)
                if len(retrieved_memories) >= 5:
                    break

        except Exception as exc:
            log.warning("recaller: episodic query failed – %s", exc)

    # ------------------------------------------------------------------
    # 2. Semantic profile (background facts, routine, relationships)
    # ------------------------------------------------------------------
    if semantic_profile is not None:
        try:
            profile_facts: list[dict] = await semantic_profile.get_relevant(
                user_id=user_id,
                intent=intent,
                entities=entities,
            )
            # Blend up to 2 semantic facts if we don't already have 5 memories
            for fact in profile_facts[:2]:
                if len(retrieved_memories) < 5:
                    retrieved_memories.append(_chunk_to_dict(fact))
        except Exception as exc:
            log.warning("recaller: semantic profile query failed – %s", exc)

    # ------------------------------------------------------------------
    # 3. Medication-specific log query
    # ------------------------------------------------------------------
    if intent == "medication_check" and episodic_memory is not None:
        try:
            today_start, today_end = _today_range()
            med_chunks = await episodic_memory.query(
                query_text="medication dose pill tablet",
                top_k=5,
                user_id=user_id,
                start_time=today_start,
                end_time=today_end,
                tags=["medication"],
            )
            for chunk in med_chunks:
                d = _chunk_to_dict(chunk)
                cid = d.get("id", "")
                # Avoid duplicates
                existing_ids = {m.get("id") for m in retrieved_memories}
                if cid not in existing_ids:
                    retrieved_memories.insert(0, d)  # medication info first
                    if len(retrieved_memories) > 5:
                        retrieved_memories = retrieved_memories[:5]
        except Exception as exc:
            log.warning("recaller: medication log query failed – %s", exc)

    # ------------------------------------------------------------------
    # 4. Resolve people from identified_people if we know the store
    # ------------------------------------------------------------------
    # If the perceiver found faces and we have a person store, add display
    # names to the identified_people list (best-effort).
    if identified_people and semantic_profile is not None:
        try:
            enriched: list[dict] = []
            for person in identified_people:
                if person.get("name"):
                    enriched.append(person)
                    continue
                pid = person.get("id", "")
                if pid:
                    info = await semantic_profile.get_person(
                        user_id=user_id, person_id=pid
                    )
                    if info:
                        enriched.append({**person, **_chunk_to_dict(info)})
                    else:
                        enriched.append(person)
                else:
                    enriched.append(person)
            identified_people = enriched
        except Exception as exc:
            log.warning("recaller: person enrichment failed – %s", exc)

    log.info(
        "recaller: retrieved %d memories, %d people identified",
        len(retrieved_memories),
        len(identified_people),
    )

    return {
        **state,
        "retrieved_memories": retrieved_memories,
        "identified_people": identified_people,
    }
