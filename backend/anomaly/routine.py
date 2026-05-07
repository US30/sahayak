"""
Routine tracker for Sahayak.

Learns a user's typical daily schedule from observed activity timestamps and
flags significant deviations as anomaly events.
"""
from __future__ import annotations

import asyncio
import json
import math
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel

from config import settings
from schemas import AnomalyEvent

log = structlog.get_logger(__name__)

# Tolerance window (minutes) within which an activity is considered on-schedule.
_TOLERANCE_MINUTES = 30


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _time_diff_minutes(a: time, b: time) -> float:
    """Absolute minute difference between two time objects (ignores date)."""
    a_min = a.hour * 60 + a.minute
    b_min = b.hour * 60 + b.minute
    diff = abs(a_min - b_min)
    return min(diff, 1440 - diff)  # account for midnight wrap


def _circular_mean(times_minutes: list[float]) -> float:
    """Circular mean for time-of-day values (in minutes, 0–1440)."""
    if not times_minutes:
        return 0.0
    period = 1440.0
    angles = [2 * math.pi * t / period for t in times_minutes]
    sin_sum = sum(math.sin(a) for a in angles)
    cos_sum = sum(math.cos(a) for a in angles)
    mean_angle = math.atan2(sin_sum, cos_sum)
    return (mean_angle * period / (2 * math.pi)) % period


# ---------------------------------------------------------------------------
# RoutineTracker
# ---------------------------------------------------------------------------


class RoutineTracker:
    """
    Tracks and evaluates a user's daily activity routine.

    A *baseline* is built from observed activity timestamps over at least 7
    calendar days.  The baseline stores per-activity circular mean times so
    drift is detected even for activities near midnight.

    Persistence is a simple JSON file; production deployments should replace
    this with LanceDB or another DB-backed store.
    """

    def __init__(self) -> None:
        self._store_path = Path(settings.FACE_DB_PATH) / "routines.json"
        # user_id -> activity -> list[float] (minutes since midnight)
        self._observations: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # user_id -> activity -> mean minute of day (float)
        self._baselines: dict[str, dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        log.info("routine_tracker.init.start")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load)
        log.info("routine_tracker.init.done", user_count=len(self._observations))

    def _load(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._store_path.exists():
            return
        raw = json.loads(self._store_path.read_text())
        for uid, activities in raw.get("observations", {}).items():
            for act, mins in activities.items():
                self._observations[uid][act] = list(mins)
        for uid, acts in raw.get("baselines", {}).items():
            self._baselines[uid] = {a: float(m) for a, m in acts.items()}

    def _save(self) -> None:
        payload = {
            "observations": {
                uid: dict(acts)
                for uid, acts in self._observations.items()
            },
            "baselines": self._baselines,
        }
        self._store_path.write_text(json.dumps(payload, indent=2))

    def _rebuild_baselines(self, user_id: str) -> None:
        acts = self._observations.get(user_id, {})
        self._baselines[user_id] = {
            act: _circular_mean(mins) for act, mins in acts.items() if mins
        }

    async def record_activity(
        self, user_id: str, activity: str, at: datetime
    ) -> list[AnomalyEvent]:
        """
        Record that *user_id* performed *activity* at *at*.

        Returns a list of AnomalyEvents if the timing deviates from baseline.
        """
        minute_of_day = float(at.hour * 60 + at.minute)

        async with self._lock:
            self._observations[user_id][activity].append(minute_of_day)
            self._rebuild_baselines(user_id)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._save)

        return await self.check_deviation(user_id, activity, at)

    async def check_deviation(
        self, user_id: str, activity: str, at: datetime
    ) -> list[AnomalyEvent]:
        """Return anomaly events if *activity* timing deviates from baseline."""
        baseline = self._baselines.get(user_id, {})
        if activity not in baseline:
            return []

        observed_minutes = float(at.hour * 60 + at.minute)
        expected_minutes = baseline[activity]
        diff = _time_diff_minutes(
            time(int(observed_minutes) // 60, int(observed_minutes) % 60),
            time(int(expected_minutes) // 60, int(expected_minutes) % 60),
        )

        if diff <= _TOLERANCE_MINUTES:
            return []

        severity = "high" if diff > 120 else "medium" if diff > 60 else "low"
        expected_h = int(expected_minutes) // 60
        expected_m = int(expected_minutes) % 60
        event = AnomalyEvent(
            user_id=user_id,
            event_type="routine_deviation",
            severity=severity,
            description=(
                f"'{activity}' occurred {diff:.0f} min off baseline "
                f"(expected ~{expected_h:02d}:{expected_m:02d})."
            ),
            timestamp=at,
            metadata={
                "activity": activity,
                "expected_minute": expected_minutes,
                "observed_minute": observed_minutes,
                "deviation_minutes": diff,
            },
        )
        log.warning(
            "routine_deviation",
            user_id=user_id,
            activity=activity,
            deviation_minutes=diff,
            severity=severity,
        )
        return [event]

    async def get_baseline(self, user_id: str) -> dict[str, Any]:
        """Return the learned baseline for the user (human-readable)."""
        raw = self._baselines.get(user_id, {})
        readable: dict[str, str] = {}
        for act, mins in raw.items():
            h = int(mins) // 60
            m = int(mins) % 60
            readable[act] = f"{h:02d}:{m:02d}"
        return {"user_id": user_id, "baseline": readable}


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

routine_router = APIRouter(prefix="/routine", tags=["routine"])


def _get_tracker(request: Request) -> RoutineTracker:
    return request.app.state.routine_tracker  # type: ignore[no-any-return]


class RecordActivityRequest(BaseModel):
    user_id: str
    activity: str
    at: datetime | None = None  # defaults to now


@routine_router.post("/record", response_model=list[AnomalyEvent])
async def record_activity(
    body: RecordActivityRequest,
    request: Request,
) -> list[AnomalyEvent]:
    tracker: RoutineTracker = _get_tracker(request)
    at = body.at or datetime.now(tz=timezone.utc)
    return await tracker.record_activity(
        user_id=body.user_id, activity=body.activity, at=at
    )


@routine_router.get("/baseline/{user_id}")
async def get_baseline(
    user_id: str,
    request: Request,
) -> dict[str, Any]:
    tracker: RoutineTracker = _get_tracker(request)
    return await tracker.get_baseline(user_id)
