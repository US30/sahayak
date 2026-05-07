"""
Episodic memory module for Sahayak.

Stores and retrieves time-stamped experience chunks using LanceDB for
vector similarity search and BGE-M3 for dense embeddings.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from config import settings
from schemas import MemoryChunk

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# PyArrow schema for the LanceDB "episodic" table
# ---------------------------------------------------------------------------
_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("user_id", pa.string()),
        pa.field("timestamp", pa.string()),          # ISO-8601 string
        pa.field("text", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), 1024)),
        pa.field("people", pa.list_(pa.string())),
        pa.field("location_lat", pa.float64()),
        pa.field("location_lon", pa.float64()),
        pa.field("tags", pa.list_(pa.string())),
        pa.field("session_id", pa.string()),
        pa.field("memory_type", pa.string()),
    ]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk_to_row(chunk: MemoryChunk) -> dict[str, Any]:
    lat = lon = float("nan")
    if chunk.location:
        lat = float(chunk.location.get("lat", float("nan")))
        lon = float(chunk.location.get("lon", float("nan")))
    ts = chunk.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return {
        "id": chunk.id,
        "user_id": chunk.user_id,
        "timestamp": ts.isoformat(),
        "text": chunk.text,
        "embedding": np.array(chunk.embedding, dtype=np.float32),
        "people": chunk.people or [],
        "location_lat": lat,
        "location_lon": lon,
        "tags": chunk.tags or [],
        "session_id": chunk.session_id,
        "memory_type": chunk.memory_type,
    }


def _row_to_chunk(row: dict[str, Any]) -> MemoryChunk:
    location: dict[str, float] | None = None
    lat = row.get("location_lat")
    lon = row.get("location_lon")
    if lat is not None and lon is not None:
        try:
            if not (np.isnan(float(lat)) or np.isnan(float(lon))):
                location = {"lat": float(lat), "lon": float(lon)}
        except (TypeError, ValueError):
            pass
    ts_raw = row["timestamp"]
    if isinstance(ts_raw, str):
        ts = datetime.fromisoformat(ts_raw)
    elif isinstance(ts_raw, (int, float)):
        ts = datetime.fromtimestamp(ts_raw / 1e6, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    return MemoryChunk(
        id=row["id"],
        user_id=row["user_id"],
        timestamp=ts,
        text=row["text"],
        embedding=list(row.get("embedding") or []),
        people=list(row.get("people") or []),
        location=location,
        tags=list(row.get("tags") or []),
        session_id=row.get("session_id", ""),
        memory_type=row.get("memory_type", "episodic"),
    )


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class EpisodicMemory:
    """
    Async episodic memory store backed by LanceDB + BGE-M3 embeddings.

    Call ``await instance.initialize()`` before first use.
    All public methods are coroutines; embedding inference runs in a
    thread-pool executor so the event loop is never blocked.
    """

    def __init__(self) -> None:
        self._db: lancedb.DBConnection | None = None
        self._table: lancedb.table.Table | None = None
        self._encoder: SentenceTransformer | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        log.info("episodic_memory.init.start", lancedb_path=settings.LANCEDB_PATH)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_init)
        log.info("episodic_memory.init.done")

    def _sync_init(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(settings.LANCEDB_PATH)
        if "episodic" not in self._db.table_names():
            empty = pa.table(
                {f.name: pa.array([], type=f.type) for f in _SCHEMA},
                schema=_SCHEMA,
            )
            self._table = self._db.create_table("episodic", data=empty)
        else:
            self._table = self._db.open_table("episodic")
        self._encoder = SentenceTransformer(settings.EMBEDDING_MODEL)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _embed(self, text: str) -> list[float]:
        loop = asyncio.get_running_loop()
        vec: np.ndarray = await loop.run_in_executor(
            None,
            lambda: self._encoder.encode(text, normalize_embeddings=True),  # type: ignore[union-attr]
        )
        return vec.tolist()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def store(self, chunk: MemoryChunk) -> str:
        """Compute embedding if missing, upsert to LanceDB, return id."""
        if not chunk.embedding:
            chunk.embedding = await self._embed(chunk.text)

        row = _chunk_to_row(chunk)

        async with self._lock:
            loop = asyncio.get_running_loop()

            def _upsert() -> None:
                assert self._table is not None
                try:
                    self._table.delete(f"id = '{chunk.id}'")
                except Exception:
                    pass
                self._table.add([row])

            await loop.run_in_executor(None, _upsert)

        log.info("episodic_memory.stored", id=chunk.id, user_id=chunk.user_id)
        return chunk.id

    async def query(
        self,
        query_text: str,
        user_id: str,
        k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryChunk]:
        """
        Vector-search memories for *user_id* matching *query_text*.

        Supported *filters* keys:
          - ``start_time`` (datetime | ISO str): lower bound on timestamp
          - ``end_time``   (datetime | ISO str): upper bound on timestamp
          - ``people``     (list[str]):          post-filter; chunk must contain
                                                 at least one of the listed names
        """
        filters = filters or {}
        query_vec = await self._embed(query_text)

        # Build LanceDB WHERE clause (timestamp stored as ISO string)
        where_parts: list[str] = [f"user_id = '{user_id}'"]

        if "start_time" in filters:
            st = filters["start_time"]
            if isinstance(st, str):
                st = datetime.fromisoformat(st)
            where_parts.append(f"timestamp >= '{st.isoformat()}'")

        if "end_time" in filters:
            et = filters["end_time"]
            if isinstance(et, str):
                et = datetime.fromisoformat(et)
            where_parts.append(f"timestamp <= '{et.isoformat()}'")

        where = " AND ".join(where_parts)
        fetch_k = k * 4  # over-fetch for people post-filter

        loop = asyncio.get_running_loop()

        def _search() -> list[dict[str, Any]]:
            assert self._table is not None
            return (
                self._table.search(np.array(query_vec, dtype=np.float32))
                .where(where, prefilter=True)
                .limit(fetch_k)
                .to_list()
            )

        rows: list[dict[str, Any]] = await loop.run_in_executor(None, _search)
        chunks = [_row_to_chunk(r) for r in rows]

        # People post-filter
        if "people" in filters and filters["people"]:
            wanted = set(filters["people"])
            chunks = [c for c in chunks if wanted.intersection(set(c.people))]

        return chunks[:k]

    async def get_recent(
        self, user_id: str, hours: int = 24, limit: int = 20
    ) -> list[MemoryChunk]:
        """Return the *limit* most recent memories within the last *hours* hours."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        loop = asyncio.get_running_loop()

        def _fetch() -> list[dict[str, Any]]:
            assert self._table is not None
            return (
                self._table.search()
                .where(
                    f"user_id = '{user_id}' AND timestamp >= '{cutoff}'",
                    prefilter=True,
                )
                .limit(limit * 2)
                .to_list()
            )

        try:
            rows: list[dict[str, Any]] = await loop.run_in_executor(None, _fetch)
        except Exception as exc:
            log.warning("get_recent fallback scan", error=str(exc))
            # Fallback: full-table pandas scan
            def _scan() -> list[dict[str, Any]]:
                assert self._table is not None
                df = self._table.to_pandas()
                df = df[df["user_id"] == user_id]
                df = df[df["timestamp"] >= cutoff]
                return df.to_dict("records")

            rows = await loop.run_in_executor(None, _scan)

        chunks = [_row_to_chunk(r) for r in rows]
        chunks.sort(key=lambda c: c.timestamp, reverse=True)
        return chunks[:limit]

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory by id. Returns True if a row was removed."""
        loop = asyncio.get_running_loop()

        async with self._lock:
            def _del() -> bool:
                assert self._table is not None
                before = self._table.count_rows()
                self._table.delete(f"id = '{memory_id}'")
                after = self._table.count_rows()
                return after < before

            try:
                return await loop.run_in_executor(None, _del)
            except Exception as exc:
                log.warning("episodic_memory.delete.error", error=str(exc))
                return False

    async def get_stats(self, user_id: str) -> dict[str, Any]:
        """Return aggregate statistics for a user's episodic memory store."""
        loop = asyncio.get_running_loop()

        def _stats() -> dict[str, Any]:
            assert self._table is not None
            df = self._table.to_pandas()
            udf = df[df["user_id"] == user_id]
            if udf.empty:
                return {"count": 0, "oldest": None, "newest": None, "total_people": 0}
            all_people: set[str] = set()
            for cell in udf["people"]:
                if cell:
                    all_people.update(cell)
            ts = udf["timestamp"].sort_values()
            return {
                "count": int(len(udf)),
                "oldest": str(ts.iloc[0]),
                "newest": str(ts.iloc[-1]),
                "total_people": len(all_people),
            }

        try:
            return await loop.run_in_executor(None, _stats)
        except Exception as exc:
            log.error("episodic_memory.stats.error", error=str(exc))
            return {"count": 0, "oldest": None, "newest": None, "total_people": 0}


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/memory", tags=["memory"])

# Module-level singleton; created on first request so startup is fast.
_service: EpisodicMemory | None = None
_init_lock = asyncio.Lock()


async def _get_service() -> EpisodicMemory:
    global _service
    if _service is None:
        async with _init_lock:
            if _service is None:
                svc = EpisodicMemory()
                await svc.initialize()
                _service = svc
    return _service


class _QueryRequest(BaseModel):
    query: str
    user_id: str
    k: int = 5
    filters: dict[str, Any] = {}


@router.post("/store")
async def api_store(chunk: MemoryChunk) -> dict[str, str]:
    """Store a memory chunk; embedding is computed server-side if not supplied."""
    svc = await _get_service()
    memory_id = await svc.store(chunk)
    return {"id": memory_id}


@router.post("/query", response_model=list[MemoryChunk])
async def api_query(req: _QueryRequest) -> list[MemoryChunk]:
    """Vector-search episodic memories."""
    svc = await _get_service()
    return await svc.query(req.query, req.user_id, req.k, req.filters)


@router.get("/{user_id}/recent", response_model=list[MemoryChunk])
async def api_recent(
    user_id: str, hours: int = 24, limit: int = 20
) -> list[MemoryChunk]:
    """Retrieve the most recent memories for a user."""
    svc = await _get_service()
    return await svc.get_recent(user_id, hours, limit)


@router.delete("/{memory_id}")
async def api_delete(memory_id: str) -> dict[str, bool]:
    """Delete a memory by id."""
    svc = await _get_service()
    deleted = await svc.delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


@router.get("/{user_id}/stats")
async def api_stats(user_id: str) -> dict[str, Any]:
    """Return aggregate statistics for a user's memory store."""
    svc = await _get_service()
    return await svc.get_stats(user_id)
