from __future__ import annotations

from datetime import datetime
from typing import Any, List, TypedDict
from uuid import uuid4

from pydantic import BaseModel, Field


class MemoryChunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    timestamp: datetime
    text: str
    embedding: List[float] = []
    people: List[str] = []
    location: dict[str, Any] | None = None  # {"lat": float, "lon": float}
    tags: List[str] = []
    session_id: str = ""
    memory_type: str = "episodic"  # episodic | semantic | procedural


class Person(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    name: str
    relationship: str
    face_embedding: List[float] = []
    last_seen: datetime | None = None
    interaction_count: int = 0
    notes: str = ""
    confirmed: bool = False  # caregiver-confirmed identity


class AgentState(TypedDict):
    query: str
    user_id: str
    retrieved_memories: List[dict[str, Any]]
    identified_people: List[dict[str, Any]]
    plan: List[str]
    response: str
    routing_decision: str  # "on_device" | "cloud"
    confidence: float
    error: str | None


class TranscriptionResult(BaseModel):
    text: str
    confidence: float
    language: str
    segments: List[dict[str, Any]] = []


class QueryRequest(BaseModel):
    query: str
    user_id: str
    context: dict[str, Any] = {}
    image_b64: str | None = None


class QueryResponse(BaseModel):
    response: str
    memories_used: List[str] = []
    routing: str
    latency_ms: float


class AnomalyEvent(BaseModel):
    user_id: str
    event_type: str  # "meal_skip" | "wandering" | "routine_deviation" | "med_skip"
    severity: str  # "low" | "medium" | "high"
    description: str
    timestamp: datetime
    metadata: dict[str, Any] = {}
