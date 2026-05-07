"""
Sahayak backend — FastAPI application entry point.
"""
from __future__ import annotations

import logging
import tempfile
import time as _time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings

# ---------------------------------------------------------------------------
# Structlog configuration (module-level, before any logger is created)
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger(__name__)

# Module-level start time for uptime tracking
_START_TIME: float = _time.monotonic()

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Startup: initialise all services and attach them to app.state.
    Shutdown: log graceful stop (services clean up via GC / their own teardown).
    """
    # Ensure data directory exists
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    # ── Memory layer ────────────────────────────────────────────────────────
    from memory.episodic import EpisodicMemory
    from memory.semantic import SemanticProfile
    from memory.graph_store import RelationshipGraph

    memory = EpisodicMemory()
    await memory.initialize()

    semantic = SemanticProfile()
    await semantic.initialize()

    graph_store = RelationshipGraph()
    await graph_store.initialize()

    # ── Perception layer ────────────────────────────────────────────────────
    from perception.asr import ASRService
    from perception.face import FaceService
    from perception.ocr import OCRService
    from perception.tts import TTSService

    asr = ASRService()
    await asr.initialize()

    face = FaceService()
    await face.initialize()

    ocr = OCRService()
    await ocr.initialize()

    tts = TTSService()
    await tts.initialize()

    # ── LLM layer ───────────────────────────────────────────────────────────
    from llm.cloud import CloudLLM
    from llm.on_device import OnDeviceLLM

    cloud_llm = CloudLLM(settings)
    on_device_llm = OnDeviceLLM(settings)

    # ── Routing layer ───────────────────────────────────────────────────────
    from routing.router import EdgeCloudRouter

    router_svc = EdgeCloudRouter()
    await router_svc.initialize()

    # ── Anomaly layer ───────────────────────────────────────────────────────
    from anomaly.detector import AnomalyDetector
    from anomaly.routine import RoutineTracker

    anomaly_detector = AnomalyDetector()
    await anomaly_detector.initialize()

    routine_tracker = RoutineTracker()
    await routine_tracker.initialize()

    # ── Federation client ───────────────────────────────────────────────────
    from federation.client import SahayakFLClient

    fl_client = SahayakFLClient()
    await fl_client.initialize()

    # ── Agent graph (last — depends on all services above) ──────────────────
    from agents.graph import AgentGraph

    agent_graph = AgentGraph()
    await agent_graph.initialize(
        episodic_memory=memory,
        semantic_profile=semantic,
        router=router_svc,
        agent_log_table=None,  # LanceDB log table — optional; wired in when available
        face_service_url="http://localhost:8000",  # self-referential; face router mounted here
        cloud_llm=cloud_llm,
    )

    # ── Attach to app.state ─────────────────────────────────────────────────
    app.state.memory = memory
    app.state.semantic = semantic
    app.state.graph_store = graph_store
    app.state.asr = asr
    app.state.face = face
    app.state.ocr = ocr
    app.state.tts = tts
    app.state.cloud_llm = cloud_llm
    app.state.on_device_llm = on_device_llm
    app.state.router_svc = router_svc
    app.state.anomaly_detector = anomaly_detector
    app.state.routine_tracker = routine_tracker
    app.state.fl_client = fl_client
    app.state.agent_graph = agent_graph

    log.info("sahayak.startup", message="Sahayak backend started")

    yield  # ── application runs here ──────────────────────────────────────

    log.info("sahayak.shutdown", message="Sahayak backend stopping")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Sahayak API",
    version="1.0.0",
    description=(
        "Cognitive prosthesis backend for dementia care — "
        "episodic memory, face recognition, anomaly detection, and a "
        "compassionate conversational agent."
    ),
    lifespan=lifespan,
)

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

# ASR — no prefix; defines its own /transcribe route
from perception.asr import router as asr_router  # noqa: E402
app.include_router(asr_router)

# TTS — no prefix; defines its own /tts route
from perception.tts import router as tts_router  # noqa: E402
app.include_router(tts_router)

# OCR — no prefix; defines its own /ocr route
from perception.ocr import router as ocr_router  # noqa: E402
app.include_router(ocr_router)

# Face — prefix /face
from perception.face import router as face_router  # noqa: E402
app.include_router(face_router)

# Episodic memory — prefix /memory
from memory.episodic import router as memory_router  # noqa: E402
app.include_router(memory_router)

# Semantic profile — prefix /profile
from memory.semantic import router as profile_router  # noqa: E402
app.include_router(profile_router)

# Agent graph — prefix /agent
from agents.graph import agent_router  # noqa: E402
app.include_router(agent_router)

# Anomaly detector — prefix /anomaly
from anomaly.detector import anomaly_router  # noqa: E402
app.include_router(anomaly_router)

# Routine tracker — prefix /routine
from anomaly.routine import routine_router  # noqa: E402
app.include_router(routine_router)

# Federation client — prefix /federation
from federation.client import federation_router  # noqa: E402
app.include_router(federation_router)

# Eval router — prefix /eval (defined in eval/judge.py)
from eval.judge import eval_router  # noqa: E402
app.include_router(eval_router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/", tags=["health"], summary="Health check")
async def health_check() -> dict[str, Any]:
    """Return service liveness and uptime."""
    return {
        "status": "ok",
        "version": "1.0.0",
        "uptime_seconds": round(_time.monotonic() - _START_TIME, 3),
    }


# ---------------------------------------------------------------------------
# WebSocket — real-time audio → transcript → agent response
# ---------------------------------------------------------------------------


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str) -> None:
    """
    Bidirectional WebSocket for real-time voice interaction.

    Client sends raw audio bytes; server responds with:
      1. {"type": "transcript", "text": "..."}  — ASR result
      2. {"type": "response",   "text": "..."}  — agent reply
    """
    await websocket.accept()
    log.info("ws.connected", user_id=user_id)

    asr_svc = websocket.app.state.asr
    agent: Any = websocket.app.state.agent_graph

    try:
        while True:
            audio_bytes: bytes = await websocket.receive_bytes()

            # Step 1: transcribe
            try:
                transcription = await asr_svc.transcribe(audio_bytes)
                transcript_text: str = transcription.text
            except Exception as exc:
                log.error("ws.asr_error", user_id=user_id, error=str(exc))
                await websocket.send_json({"type": "error", "text": str(exc)})
                continue

            await websocket.send_json({"type": "transcript", "text": transcript_text})

            # Step 2: run agent pipeline
            if transcript_text.strip():
                try:
                    query_response = await agent.arun(
                        query=transcript_text,
                        user_id=user_id,
                    )
                    agent_text: str = query_response.response
                except Exception as exc:
                    log.error("ws.agent_error", user_id=user_id, error=str(exc))
                    await websocket.send_json({"type": "error", "text": str(exc)})
                    continue

                await websocket.send_json({"type": "response", "text": agent_text})

    except WebSocketDisconnect:
        log.info("ws.disconnected", user_id=user_id)
    except Exception as exc:
        log.error("ws.unexpected_error", user_id=user_id, error=str(exc))
        try:
            await websocket.send_json({"type": "error", "text": str(exc)})
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def global_exception_handler(request: Any, exc: Exception) -> JSONResponse:
    log.error(
        "unhandled_exception",
        error=str(exc),
        exc_type=type(exc).__name__,
        path=str(request.url),
    )
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "type": type(exc).__name__},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=True,
    )
