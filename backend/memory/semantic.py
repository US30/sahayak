"""
Semantic memory module for Sahayak.

Stores persistent, structured facts about the user (name, preferences,
medical conditions, medications, daily routine) in LanceDB.  Unlike
episodic memory, semantic profiles are not time-indexed narrative events
but stable factual knowledge that is continuously updated.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# LanceDB schema for the "semantic_profiles" table
# ---------------------------------------------------------------------------
# We store the entire profile as a JSON blob so the schema stays fixed while
# the profile dict can hold arbitrary nested structures.
_PROFILE_SCHEMA = pa.schema(
    [
        pa.field("user_id", pa.string()),
        pa.field("profile_json", pa.string()),   # JSON-serialised dict
        pa.field("updated_at", pa.string()),     # ISO-8601
    ]
)

# ---------------------------------------------------------------------------
# Default profile skeleton
# ---------------------------------------------------------------------------
_DEFAULT_PROFILE: dict[str, Any] = {
    "name": "",
    "age": None,
    "gender": "",
    "language": "hi",
    "preferences": {},
    "medical_conditions": [],
    "allergies": [],
    "medications": [],           # list of MedicationEntry dicts
    "medication_log": [],        # list of MedicationLogEntry dicts
    "family_facts": {},
    "daily_routine": {
        "wake_time": None,
        "meal_times": {"breakfast": None, "lunch": None, "dinner": None},
        "sleep_time": None,
        "exercise_time": None,
    },
    "emergency_contacts": [],
    "notes": "",
}


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *updates* into *base* without losing existing keys."""
    result = dict(base)
    for key, value in updates.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class SemanticProfile:
    """
    Persistent semantic profile store backed by LanceDB.

    Each user gets one row in the ``semantic_profiles`` table; updates are
    merge-applied so partial updates never clobber existing fields.

    Call ``await instance.initialize()`` before first use.
    """

    def __init__(self) -> None:
        self._db: lancedb.DBConnection | None = None
        self._table: lancedb.table.Table | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        log.info("semantic_profile.init.start")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_init)
        log.info("semantic_profile.init.done")

    def _sync_init(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(settings.LANCEDB_PATH)
        if "semantic_profiles" not in self._db.table_names():
            empty = pa.table(
                {f.name: pa.array([], type=f.type) for f in _PROFILE_SCHEMA},
                schema=_PROFILE_SCHEMA,
            )
            self._table = self._db.create_table("semantic_profiles", data=empty)
        else:
            self._table = self._db.open_table("semantic_profiles")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_profile_sync(self, user_id: str) -> dict[str, Any] | None:
        assert self._table is not None
        try:
            rows = (
                self._table.search()
                .where(f"user_id = '{user_id}'", prefilter=True)
                .limit(1)
                .to_list()
            )
        except Exception:
            rows = []
        if not rows:
            return None
        return json.loads(rows[0]["profile_json"])

    def _write_profile_sync(self, user_id: str, profile: dict[str, Any]) -> None:
        assert self._table is not None
        try:
            self._table.delete(f"user_id = '{user_id}'")
        except Exception:
            pass
        row = {
            "user_id": user_id,
            "profile_json": json.dumps(profile),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._table.add([row])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        """Return the full semantic profile for *user_id*."""
        loop = asyncio.get_running_loop()
        profile = await loop.run_in_executor(None, self._read_profile_sync, user_id)
        if profile is None:
            return dict(_DEFAULT_PROFILE)
        return profile

    async def update_profile(self, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """
        Deep-merge *updates* into the existing profile and persist.

        Returns the full updated profile.
        """
        async with self._lock:
            loop = asyncio.get_running_loop()
            existing = await loop.run_in_executor(None, self._read_profile_sync, user_id)
            if existing is None:
                existing = dict(_DEFAULT_PROFILE)
            merged = _deep_merge(existing, updates)
            await loop.run_in_executor(None, self._write_profile_sync, user_id, merged)
        log.info("semantic_profile.updated", user_id=user_id)
        return merged

    async def get_medications(self, user_id: str) -> list[dict[str, Any]]:
        """
        Return the medication schedule for *user_id*.

        Each entry is expected to have the shape::

            {
                "name": str,
                "dosage": str,
                "frequency": str,        # e.g. "twice daily"
                "times": list[str],      # e.g. ["08:00", "20:00"]
                "with_food": bool,
                "notes": str,
            }
        """
        profile = await self.get_profile(user_id)
        return profile.get("medications", [])

    async def update_medication_log(
        self,
        user_id: str,
        med_name: str,
        taken: bool,
        timestamp: datetime,
    ) -> None:
        """
        Append a medication taken/skipped event to the user's log.

        The log grows unboundedly in this implementation; a production system
        would archive entries older than N days to cold storage.
        """
        log_entry = {
            "med_name": med_name,
            "taken": taken,
            "timestamp": timestamp.isoformat(),
        }
        async with self._lock:
            loop = asyncio.get_running_loop()
            profile = await loop.run_in_executor(None, self._read_profile_sync, user_id)
            if profile is None:
                profile = dict(_DEFAULT_PROFILE)
            profile.setdefault("medication_log", []).append(log_entry)
            await loop.run_in_executor(None, self._write_profile_sync, user_id, profile)
        log.info(
            "medication_log.updated",
            user_id=user_id,
            med=med_name,
            taken=taken,
        )


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/profile", tags=["profile"])

_service: SemanticProfile | None = None
_init_lock = asyncio.Lock()


async def _get_service() -> SemanticProfile:
    global _service
    if _service is None:
        async with _init_lock:
            if _service is None:
                svc = SemanticProfile()
                await svc.initialize()
                _service = svc
    return _service


class _ProfileUpdate(BaseModel):
    updates: dict[str, Any]


class _MedLogRequest(BaseModel):
    taken: bool
    timestamp: datetime | None = None


@router.get("/{user_id}")
async def api_get_profile(user_id: str) -> dict[str, Any]:
    """Retrieve the full semantic profile for a user."""
    svc = await _get_service()
    return await svc.get_profile(user_id)


@router.post("/{user_id}")
async def api_update_profile(user_id: str, body: _ProfileUpdate) -> dict[str, Any]:
    """Deep-merge updates into the user's semantic profile."""
    svc = await _get_service()
    return await svc.update_profile(user_id, body.updates)


@router.get("/{user_id}/medications")
async def api_get_medications(user_id: str) -> list[dict[str, Any]]:
    """Return the medication schedule for a user."""
    svc = await _get_service()
    return await svc.get_medications(user_id)


@router.post("/{user_id}/medications/{med_name}/log")
async def api_log_medication(
    user_id: str,
    med_name: str,
    body: _MedLogRequest,
) -> dict[str, str]:
    """Log that a medication was taken or skipped."""
    svc = await _get_service()
    ts = body.timestamp or datetime.now(timezone.utc)
    await svc.update_medication_log(user_id, med_name, body.taken, ts)
    return {"status": "logged"}
