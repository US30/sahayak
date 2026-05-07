"""
AgentGraph — Sahayak's four-node LangGraph pipeline.

Topology
--------
    START → perceiver → recaller → planner ──(conditional)──→ speaker → END

The conditional edge after planner routes to speaker in all cases; speaker
internally handles the low-confidence / insufficient-context fallback so the
graph topology stays simple and future nodes can be inserted without
structural changes.

FastAPI endpoints (mounted at /agent)
--------------------------------------
    POST /agent/query              – run one query through the pipeline
    GET  /agent/{user_id}/history  – last 20 interactions from agent_logs
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from langgraph.graph import END, START, StateGraph

from schemas import AgentState, QueryRequest, QueryResponse

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Sahayak persona – shared by speaker and planner
# ---------------------------------------------------------------------------

_SAHAYAK_SYSTEM = (
    "You are Sahayak, a warm and patient memory assistant for a person with mild "
    "cognitive impairment. Answer in simple, reassuring language. Use the person's "
    "language (Hindi/English mix as appropriate). Keep responses under 3 sentences. "
    "Never express uncertainty harshly — always be gentle and supportive."
)

_FALLBACK_RESPONSE = (
    "Mujhe abhi yeh yaad nahi aa raha, lekin koi baat nahi. "
    "Aap apne caregiver se pooch sakte hain, ya thodi der mein dobara try karein."
)

# ---------------------------------------------------------------------------
# Internal node helpers
# ---------------------------------------------------------------------------


def _chunk_to_dict(chunk: Any) -> dict:
    if isinstance(chunk, dict):
        return chunk
    if hasattr(chunk, "model_dump"):
        return chunk.model_dump()
    return dict(chunk)


# ── Perceiver ────────────────────────────────────────────────────────────────

async def _perceiver(state: AgentState, cfg: dict) -> AgentState:
    """
    Analyse the query + optional image.

    • Calls face-recognition service if image_b64 is present.
    • Extracts entities and intent via CloudLLM (JSON output).
    • Resolves relative time references to concrete datetime ranges.
    """
    import httpx, re
    from datetime import timedelta

    cloud_llm = cfg.get("cloud_llm")
    face_url: str = cfg.get("face_service_url", "http://localhost:8001")

    query: str = state.get("query", "")
    image_b64: str | None = state.get("image_b64")
    context: dict = dict(state.get("context", {}))
    identified_people: list[dict] = list(state.get("identified_people", []))

    # Face recognition
    if image_b64:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(f"{face_url}/face/recognize",
                                      json={"image_b64": image_b64})
                r.raise_for_status()
                identified_people.extend(r.json().get("people", []))
        except Exception as exc:
            log.warning("perceiver.face_service_error", error=str(exc))

    # Entity + intent extraction
    _ENTITY_SYSTEM = (
        "Extract entities and intent from the user query. "
        "Return ONLY valid JSON: "
        '{"entities":{"people":[],"locations":[],"time_references":[]},'
        '"intent":"recall_person|recall_event|medication_check|routine_check|general"}'
    )
    extracted: dict = {"entities": {"people": [], "locations": [], "time_references": []},
                       "intent": "general"}
    if cloud_llm is not None:
        try:
            raw = await cloud_llm.complete(
                system=_ENTITY_SYSTEM,
                messages=[{"role": "user", "content": query}],
                max_tokens=256,
                temperature=0.0,
            )
            clean = raw.strip().strip("```").strip()
            extracted = json.loads(clean)
        except Exception as exc:
            log.warning("perceiver.entity_extraction_failed", error=str(exc))

    # Time-reference resolution
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    _WINDOWS = {"morning": (6, 12), "afternoon": (12, 17), "evening": (17, 21),
                "night": (21, 24), "today": (0, 24)}
    time_range: dict = {}
    for ref in extracted.get("entities", {}).get("time_references", []):
        rl = ref.lower()
        if "yesterday" in rl:
            time_range = {"start": (today - timedelta(days=1)).isoformat(),
                          "end": today.isoformat()}
            break
        for key, (h0, h1) in _WINDOWS.items():
            if key in rl:
                time_range = {"start": (today + timedelta(hours=h0)).isoformat(),
                              "end": (today + timedelta(hours=h1)).isoformat()}
                break
        m = re.search(r"last\s+(\d+)\s+hour", rl)
        if m:
            time_range = {"start": (now - timedelta(hours=int(m.group(1)))).isoformat(),
                          "end": now.isoformat()}
            break
        if time_range:
            break

    context["entities"] = extracted.get("entities", {})
    context["intent"] = extracted.get("intent", "general")
    context["time_range"] = time_range

    log.info("perceiver.done", intent=context["intent"],
             people=context["entities"].get("people", []))
    return {**state, "context": context, "identified_people": identified_people}


# ── Recaller ─────────────────────────────────────────────────────────────────

async def _recaller(state: AgentState, cfg: dict) -> AgentState:
    """
    Pull episodic memories and semantic profile facts.

    • For medication_check intent: prioritise today's med-log entries.
    • Returns up to 5 most-relevant MemoryChunk dicts.
    """
    from datetime import timedelta

    episodic = cfg.get("episodic_memory")
    profile = cfg.get("semantic_profile")

    context: dict = state.get("context", {})
    intent: str = context.get("intent", "general")
    entities: dict = context.get("entities", {})
    time_range: dict = context.get("time_range", {})
    user_id: str = state.get("user_id", "")
    query: str = state.get("query", "")

    memories: list[dict] = []
    seen_ids: set[str] = set()

    def _add(chunk: Any) -> None:
        d = _chunk_to_dict(chunk)
        cid = d.get("id", "")
        if cid and cid in seen_ids:
            return
        if cid:
            seen_ids.add(cid)
        if len(memories) < 5:
            memories.append(d)

    if episodic is not None:
        try:
            kw: dict = {"user_id": user_id}
            if time_range:
                kw["start_time"] = time_range.get("start")
                kw["end_time"] = time_range.get("end")
            people = entities.get("people", [])
            if people:
                kw["people_names"] = people
            for c in await episodic.query(query_text=query, top_k=10, **kw):
                _add(c)
        except Exception as exc:
            log.warning("recaller.episodic_query_failed", error=str(exc))

    # Medication-specific pass
    if intent == "medication_check" and episodic is not None:
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            med_chunks = await episodic.query(
                query_text="medication dose pill tablet",
                top_k=5,
                user_id=user_id,
                start_time=today.isoformat(),
                end_time=(today + timedelta(days=1)).isoformat(),
                tags=["medication"],
            )
            for c in med_chunks:
                d = _chunk_to_dict(c)
                # Insert medication entries at the front
                cid = d.get("id", "")
                if cid not in seen_ids:
                    if cid:
                        seen_ids.add(cid)
                    memories.insert(0, d)
                    if len(memories) > 5:
                        memories.pop()
        except Exception as exc:
            log.warning("recaller.med_log_query_failed", error=str(exc))

    # Blend ≤2 semantic facts when we have room
    if profile is not None:
        try:
            facts = await profile.get_relevant(
                user_id=user_id, intent=intent, entities=entities)
            for f in facts[:2]:
                _add(f)
        except Exception as exc:
            log.warning("recaller.semantic_profile_failed", error=str(exc))

    log.info("recaller.done", n_memories=len(memories))
    return {**state, "retrieved_memories": memories,
            "identified_people": state.get("identified_people", [])}


# ── Planner ──────────────────────────────────────────────────────────────────

_INTENT_PLANS: dict[str, list[str]] = {
    "recall_person": ["identify_person_from_memories", "describe_meeting_context", "compose_answer"],
    "recall_event":  ["find_event_in_memories", "determine_time_and_place", "compose_answer"],
    "medication_check": ["check_med_log", "check_routine", "compose_answer"],
    "routine_check": ["check_routine", "compare_with_schedule", "compose_answer"],
    "general":       ["search_relevant_memories", "compose_answer"],
}
_INSUFFICIENT_PLAN = ["insufficient_context", "use_semantic_profile", "compose_gentle_apology"]


async def _planner(state: AgentState, cfg: dict) -> AgentState:
    """
    Decide response plan, confidence, and routing target.
    """
    router = cfg.get("router")
    memories: list[dict] = state.get("retrieved_memories", [])
    intent: str = state.get("context", {}).get("intent", "general")
    query: str = state.get("query", "")

    has_memories = len(memories) > 0
    if not has_memories:
        plan = _INSUFFICIENT_PLAN.copy()
        confidence: float = 0.1
    else:
        plan = _INTENT_PLANS.get(intent, _INTENT_PLANS["general"]).copy()
        if intent == "medication_check":
            med_tagged = any("medication" in (m.get("tags") or []) for m in memories)
            confidence = 0.85 if med_tagged else 0.55
        elif len(memories) >= 3:
            confidence = 0.80
        else:
            confidence = 0.60

    # Routing: delegate to EdgeCloudRouter's complexity scorer
    routing_decision = "cloud"
    if router is not None:
        try:
            # router.route() returns (text, decision, score); we only need decision+score
            _, routing_decision, _ = await router.route(
                query=query,
                system_prompt=_SAHAYAK_SYSTEM,
            )
        except Exception as exc:
            log.warning("planner.routing_failed", error=str(exc))

    log.info("planner.done", intent=intent, plan=plan,
             confidence=confidence, routing=routing_decision)
    return {**state, "plan": plan, "confidence": confidence,
            "routing_decision": routing_decision}


# ── Speaker ───────────────────────────────────────────────────────────────────

async def _speaker(state: AgentState, cfg: dict) -> AgentState:
    """
    Generate the final compassionate response.

    Picks LLM backend based on routing_decision; low-confidence falls back
    to a gentle pre-written message.
    """
    plan: list[str] = state.get("plan", [])
    confidence: float = state.get("confidence", 0.5)
    routing: str = state.get("routing_decision", "cloud")

    if confidence < 0.2 or "insufficient_context" in plan:
        log.info("speaker.fallback_response")
        return {**state, "response": _FALLBACK_RESPONSE}

    router = cfg.get("router")
    memories: list[dict] = state.get("retrieved_memories", [])
    people: list[dict] = state.get("identified_people", [])

    mem_ctx = "\n".join(f"- {m.get('text','')}" for m in memories) or "(no memories)"
    ppl_ctx = ", ".join(
        f"{p.get('name','?')} ({p.get('relationship','')})" for p in people
    ) or "none identified"
    plan_text = " → ".join(plan)

    full_query = (
        f"User question: {state.get('query','')}\n\n"
        f"Response plan: {plan_text}\n\n"
        f"People known: {ppl_ctx}\n\n"
        f"Memory context:\n{mem_ctx}\n\n"
        "Please answer the question using the context above."
    )

    response_text: str = ""
    used_routing = routing

    if router is not None:
        try:
            response_text, used_routing, _ = await router.route(
                query=full_query,
                system_prompt=_SAHAYAK_SYSTEM,
            )
        except Exception as exc:
            log.error("speaker.router_failed", error=str(exc))

    if not response_text:
        response_text = _FALLBACK_RESPONSE

    log.info("speaker.done", routing=used_routing,
             response_len=len(response_text))
    return {**state, "response": response_text, "routing_decision": used_routing}


# ---------------------------------------------------------------------------
# Conditional edge
# ---------------------------------------------------------------------------

def _after_planner(state: AgentState) -> str:
    """Always route to speaker; speaker handles low-confidence internally."""
    return "speaker"


# ---------------------------------------------------------------------------
# AgentGraph
# ---------------------------------------------------------------------------


class AgentGraph:
    """
    Compiled LangGraph pipeline for Sahayak.

    Call ``initialize()`` once at startup, then ``arun()`` per query.
    """

    def __init__(self) -> None:
        self._graph: Any = None
        self._episodic_memory: Any = None
        self._semantic_profile: Any = None
        self._router: Any = None
        self._agent_log_table: Any = None
        self._face_service_url: str = "http://localhost:8001"

    async def initialize(
        self,
        episodic_memory: Any,
        semantic_profile: Any,
        router: Any,
        agent_log_table: Any = None,
        face_service_url: str = "http://localhost:8001",
        cloud_llm: Any = None,
    ) -> None:
        log.info("agent_graph.init.start")
        self._episodic_memory = episodic_memory
        self._semantic_profile = semantic_profile
        self._router = router
        self._agent_log_table = agent_log_table
        self._face_service_url = face_service_url
        self._cloud_llm = cloud_llm
        self._graph = self._build()
        log.info("agent_graph.init.done")

    def _cfg(self) -> dict:
        return {
            "cloud_llm": getattr(self, "_cloud_llm", None),
            "router": self._router,
            "episodic_memory": self._episodic_memory,
            "semantic_profile": self._semantic_profile,
            "face_service_url": self._face_service_url,
        }

    def _build(self) -> Any:
        cfg = self._cfg()

        async def perceiver_node(s: AgentState) -> AgentState:
            return await _perceiver(s, cfg)

        async def recaller_node(s: AgentState) -> AgentState:
            return await _recaller(s, cfg)

        async def planner_node(s: AgentState) -> AgentState:
            return await _planner(s, cfg)

        async def speaker_node(s: AgentState) -> AgentState:
            return await _speaker(s, cfg)

        g: StateGraph = StateGraph(AgentState)
        g.add_node("perceiver", perceiver_node)
        g.add_node("recaller", recaller_node)
        g.add_node("planner", planner_node)
        g.add_node("speaker", speaker_node)

        g.add_edge(START, "perceiver")
        g.add_edge("perceiver", "recaller")
        g.add_edge("recaller", "planner")
        g.add_conditional_edges("planner", _after_planner, {"speaker": "speaker"})
        g.add_edge("speaker", END)

        return g.compile()

    # ── Public run method ────────────────────────────────────────────────

    async def arun(
        self,
        query: str,
        user_id: str,
        context: dict | None = None,
        image_b64: str | None = None,
    ) -> QueryResponse:
        """Execute the full pipeline and return a QueryResponse."""
        run_id = str(uuid.uuid4())
        t0 = time.perf_counter()

        initial: AgentState = {
            "query": query,
            "user_id": user_id,
            "retrieved_memories": [],
            "identified_people": [],
            "plan": [],
            "response": "",
            "routing_decision": "cloud",
            "confidence": 0.5,
            "error": None,
            "image_b64": image_b64,
            "context": context or {},
        }

        try:
            final: AgentState = await self._graph.ainvoke(initial)
        except Exception as exc:
            log.exception("agent_graph.pipeline_error", error=str(exc))
            final = {
                **initial,
                "response": _FALLBACK_RESPONSE,
                "error": str(exc),
            }

        latency_ms = (time.perf_counter() - t0) * 1000.0
        self._persist_log(final, latency_ms, run_id)

        return QueryResponse(
            response=final.get("response", ""),
            memories_used=[
                m.get("id", "") for m in final.get("retrieved_memories", [])
                if m.get("id")
            ],
            routing=final.get("routing_decision", "cloud"),
            latency_ms=round(latency_ms, 2),
        )

    # Backwards-compat shim used by earlier callers
    async def run(
        self,
        query: str,
        user_id: str,
        context: dict | None = None,
    ) -> AgentState:
        """Legacy interface — prefer arun()."""
        resp = await self.arun(query=query, user_id=user_id, context=context)
        return {
            "query": query,
            "user_id": user_id,
            "retrieved_memories": [],
            "identified_people": [],
            "plan": [],
            "response": resp.response,
            "routing_decision": resp.routing,
            "confidence": 0.0,
            "error": None,
            "image_b64": None,
            "context": context or {},
        }

    def _persist_log(self, state: AgentState, latency_ms: float, run_id: str) -> None:
        table = self._agent_log_table
        if table is None:
            return
        try:
            table.add([{
                "id": run_id,
                "user_id": state.get("user_id", ""),
                "query": state.get("query", ""),
                "response": state.get("response", ""),
                "routing": state.get("routing_decision", "cloud"),
                "confidence": float(state.get("confidence", 0.0)),
                "plan": json.dumps(state.get("plan", [])),
                "memories_used": json.dumps(
                    [m.get("id", "") for m in state.get("retrieved_memories", [])]
                ),
                "latency_ms": latency_ms,
                "timestamp": datetime.utcnow().isoformat(),
                "error": state.get("error") or "",
            }])
        except Exception as exc:
            log.warning("agent_graph.log_persist_failed", error=str(exc))


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

agent_router = APIRouter(prefix="/agent", tags=["agent"])


def _get_graph(request: Request) -> AgentGraph:
    graph: AgentGraph | None = getattr(request.app.state, "agent_graph", None)
    if graph is None:
        raise HTTPException(503, "Agent graph not initialised yet.")
    return graph


def _get_log_table(request: Request) -> Any:
    return getattr(request.app.state, "agent_log_table", None)


@agent_router.post("/query", response_model=QueryResponse,
                   summary="Run the Sahayak agent pipeline")
async def query_agent(body: QueryRequest, request: Request) -> QueryResponse:
    """Submit a query; runs Perceiver → Recaller → Planner → Speaker."""
    graph = _get_graph(request)
    return await graph.arun(
        query=body.query,
        user_id=body.user_id,
        context=body.context,
        image_b64=body.image_b64,
    )


@agent_router.get("/{user_id}/history",
                  summary="Last 20 agent interactions for a user")
async def history_endpoint(user_id: str, request: Request) -> list[dict]:
    """Return the 20 most-recent interaction records from ``agent_logs``."""
    table = _get_log_table(request)
    if table is None:
        raise HTTPException(503, "Agent log table not available.")
    try:
        df = (
            table.search()
            .where(f"user_id = '{user_id}'")
            .limit(20)
            .to_pandas()
        )
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp", ascending=False)
        records: list[dict] = df.to_dict(orient="records")
        for rec in records:
            for field in ("plan", "memories_used"):
                if isinstance(rec.get(field), str):
                    try:
                        rec[field] = json.loads(rec[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
        return records
    except Exception as exc:
        log.exception("history_endpoint.failed", user_id=user_id, error=str(exc))
        raise HTTPException(500, f"Failed to retrieve history: {exc}")
