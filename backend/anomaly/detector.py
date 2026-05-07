"""
anomaly/detector.py — AnomalyDetector

Monitors users' routines and memory entries for anomalies.  Supports both
event-driven rule evaluation (immediate) and background polling (scheduled).

Anomaly types detected:
  - meal_skip      : Expected meal time passed >1 h with no meal memory
  - med_skip       : Medication time passed >2 h with no log
  - wandering      : GPS >2 km from home during 22:00–06:00
  - routine_deviation : Any routine event >2 h outside expected window
  - silence        : No memory entries for >4 h during waking hours
  - repeated_query : Same question asked ≥3 times in a session (cognitive signal)
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from schemas import AnomalyEvent

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEAL_SKIP_GRACE_HOURS: float = 1.0
MED_SKIP_GRACE_HOURS: float = 2.0
WANDERING_DIST_KM: float = 2.0
WANDERING_NIGHT_START: int = 22
WANDERING_NIGHT_END: int = 6
ROUTINE_DEVIATION_HOURS: float = 2.0
SILENCE_HOURS: float = 4.0
_MAX_LOG: int = 1_000


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Built-in rule functions  (pure, sync, testable)
# ---------------------------------------------------------------------------


def _rule_meal_skip(ctx: dict[str, Any]) -> AnomalyEvent | None:
    """Trigger if a meal time was explicitly recorded as skipped."""
    if ctx.get("event_type") != "meal_record":
        return None
    if ctx.get("eaten") is False:
        return AnomalyEvent(
            user_id=ctx["user_id"],
            event_type="meal_skip",
            severity="medium",
            description=f"Meal skipped: {ctx.get('meal', 'unknown')}",
            timestamp=datetime.now(tz=timezone.utc),
            metadata={"meal": ctx.get("meal"), "reason": ctx.get("reason", "")},
        )
    return None


def _rule_no_activity(ctx: dict[str, Any]) -> AnomalyEvent | None:
    """Trigger if device inactivity exceeds threshold (default 4 hours)."""
    if ctx.get("event_type") != "inactivity_check":
        return None
    idle_hours: float = ctx.get("idle_hours", 0.0)
    threshold: float = ctx.get("threshold_hours", 4.0)
    if idle_hours >= threshold:
        severity = "high" if idle_hours >= 8.0 else "medium"
        return AnomalyEvent(
            user_id=ctx["user_id"],
            event_type="silence",
            severity=severity,
            description=f"No device activity for {idle_hours:.1f} hours.",
            timestamp=datetime.now(tz=timezone.utc),
            metadata={"idle_hours": idle_hours},
        )
    return None


def _rule_repeated_query(ctx: dict[str, Any]) -> AnomalyEvent | None:
    """Trigger if the same question is asked more than 3 times in a session."""
    if ctx.get("event_type") != "query_record":
        return None
    count: int = ctx.get("repeat_count", 0)
    if count >= 3:
        return AnomalyEvent(
            user_id=ctx["user_id"],
            event_type="routine_deviation",
            severity="low",
            description=(
                f"Question repeated {count} times this session: "
                f"\"{ctx.get('query', '')[:80]}\""
            ),
            timestamp=datetime.now(tz=timezone.utc),
            metadata={"query": ctx.get("query"), "count": count},
        )
    return None


# ---------------------------------------------------------------------------
# AnomalyDetector
# ---------------------------------------------------------------------------


class AnomalyDetector:
    """
    Dual-mode anomaly detector for Sahayak.

    1. **Event-driven**: call ``evaluate(context)`` with any event dict to
       run all registered rule functions synchronously.

    2. **Polling**: call ``check_all(user_id, ...)`` to cross-check the
       RoutineTracker and EpisodicMemory for meal skips, medication misses,
       wandering, silence, and routine deviations.

    3. **Background monitoring**: ``start_monitoring(user_id, ...)`` launches
       an asyncio task that calls ``check_all`` on an interval and POSTs
       high-severity alerts to a caregiver webhook.
    """

    def __init__(self) -> None:
        self._rules: list[Callable[[dict[str, Any]], AnomalyEvent | None]] = []
        # Per-user lists of unresolved anomalies
        self._active: dict[str, list[AnomalyEvent]] = {}
        # All-time log (bounded)
        self._log: list[AnomalyEvent] = []
        # Stable ID lookup (event_type -> AnomalyEvent) — keyed by injected id
        self._registry: dict[str, AnomalyEvent] = {}
        self._monitor_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        log.info("anomaly_detector.init.start")
        self._register_builtin_rules()
        log.info("anomaly_detector.init.done", rule_count=len(self._rules))

    def _register_builtin_rules(self) -> None:
        self.register_rule(_rule_meal_skip)
        self.register_rule(_rule_no_activity)
        self.register_rule(_rule_repeated_query)

    def register_rule(
        self, fn: Callable[[dict[str, Any]], AnomalyEvent | None]
    ) -> None:
        """Register a custom rule function."""
        self._rules.append(fn)

    # ------------------------------------------------------------------
    # Internal persistence helpers
    # ------------------------------------------------------------------

    def _attach_id(self, evt: AnomalyEvent) -> str:
        """Attach a stable UUID to an AnomalyEvent and return it."""
        eid = str(uuid4())
        object.__setattr__(evt, "_id", eid)
        return eid

    def _event_id(self, evt: AnomalyEvent) -> str:
        return getattr(evt, "_id", evt.event_type + evt.timestamp.isoformat())

    async def _record(self, user_id: str, evt: AnomalyEvent) -> str:
        """Persist an event to the active queue, log, and registry."""
        eid = self._attach_id(evt)
        async with self._lock:
            self._active.setdefault(user_id, []).append(evt)
            self._log.append(evt)
            if len(self._log) > _MAX_LOG:
                self._log = self._log[-_MAX_LOG:]
            self._registry[eid] = evt
        return eid

    # ------------------------------------------------------------------
    # Event-driven evaluation (rule-based)
    # ------------------------------------------------------------------

    async def evaluate(self, context: dict[str, Any]) -> list[AnomalyEvent]:
        """Run all registered rules against *context* and persist triggered events."""
        triggered: list[AnomalyEvent] = []
        for rule in self._rules:
            try:
                event = rule(context)
                if event is not None:
                    triggered.append(event)
            except Exception:
                log.exception("anomaly_detector.rule_error", rule=rule.__name__)

        for evt in triggered:
            user_id = evt.user_id
            await self._record(user_id, evt)
            log.warning(
                "anomaly_detected",
                user_id=user_id,
                type=evt.event_type,
                severity=evt.severity,
            )

        return triggered

    async def get_recent(
        self,
        user_id: str,
        limit: int = 50,
        severity: str | None = None,
    ) -> list[AnomalyEvent]:
        """Return recent anomalies from the bounded log."""
        async with self._lock:
            events = [e for e in self._log if e.user_id == user_id]
        if severity:
            events = [e for e in events if e.severity == severity]
        return list(reversed(events))[:limit]

    # ------------------------------------------------------------------
    # Polling-based checks (cross-references routine + memory)
    # ------------------------------------------------------------------

    async def _check_meal_skip(
        self,
        user_id: str,
        routine: dict[str, Any],
        memory_chunks: list[Any],
        now: datetime,
    ) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        now_hour = now.hour + now.minute / 60.0
        today_str = now.date().isoformat()
        meal_times: dict[str, list[float]] = routine.get("meal_times", {})

        for meal_name, window in meal_times.items():
            expected_end = window[1]
            if now_hour < expected_end + MEAL_SKIP_GRACE_HOURS:
                continue
            found = any(
                today_str in getattr(c, "timestamp", now).isoformat()
                and (
                    any(
                        t.lower() in (meal_name, "meal", "khana", "bhojan")
                        for t in (getattr(c, "tags", []) or [])
                    )
                    or meal_name in getattr(c, "text", "").lower()
                )
                for c in memory_chunks
            )
            if not found:
                overdue = now_hour - (expected_end + MEAL_SKIP_GRACE_HOURS)
                severity = "high" if overdue >= 2 else "medium"
                events.append(
                    AnomalyEvent(
                        user_id=user_id,
                        event_type="meal_skip",
                        severity=severity,
                        description=(
                            f"{meal_name.capitalize()} not recorded — expected "
                            f"{int(window[0]):02d}:00–{int(window[1]):02d}:59, "
                            f"now {now.strftime('%H:%M')}."
                        ),
                        timestamp=now,
                        metadata={
                            "meal_name": meal_name,
                            "expected_window": window,
                            "overdue_hours": round(overdue, 2),
                        },
                    )
                )
        return events

    async def _check_med_skip(
        self,
        user_id: str,
        routine: dict[str, Any],
        memory_chunks: list[Any],
        now: datetime,
    ) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        now_hour = now.hour + now.minute / 60.0
        today_str = now.date().isoformat()

        for med in routine.get("medication_schedule", []):
            med_name: str = med["name"]
            for time_str in med.get("times", []):
                try:
                    h, m = (int(x) for x in time_str.split(":"))
                    scheduled_hour = h + m / 60.0
                except ValueError:
                    continue
                if now_hour < scheduled_hour + MED_SKIP_GRACE_HOURS:
                    continue
                found = any(
                    today_str in getattr(c, "timestamp", now).isoformat()
                    and (
                        "medication" in (getattr(c, "tags", []) or [])
                        or med_name.lower() in getattr(c, "text", "").lower()
                        or "dawa" in getattr(c, "text", "").lower()
                    )
                    for c in memory_chunks
                )
                if not found:
                    overdue = now_hour - (scheduled_hour + MED_SKIP_GRACE_HOURS)
                    events.append(
                        AnomalyEvent(
                            user_id=user_id,
                            event_type="med_skip",
                            severity="high",
                            description=(
                                f"{med_name} at {time_str} not recorded. "
                                f"Overdue by {overdue:.1f} h."
                            ),
                            timestamp=now,
                            metadata={
                                "medication_name": med_name,
                                "scheduled_time": time_str,
                                "overdue_hours": round(overdue, 2),
                            },
                        )
                    )
        return events

    async def _check_wandering(
        self,
        user_id: str,
        memory_chunks: list[Any],
        now: datetime,
        home_location: dict[str, float] | None,
    ) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        hour = now.hour
        is_night = hour >= WANDERING_NIGHT_START or hour < WANDERING_NIGHT_END
        if not is_night or home_location is None:
            return events

        home_lat = home_location.get("lat", 0.0)
        home_lon = home_location.get("lon", 0.0)

        for chunk in memory_chunks:
            loc = getattr(chunk, "location", None)
            if not loc:
                continue
            chunk_ts: datetime = getattr(chunk, "timestamp", now)
            ch = chunk_ts.hour
            if not (ch >= WANDERING_NIGHT_START or ch < WANDERING_NIGHT_END):
                continue
            lat, lon = loc.get("lat"), loc.get("lon")
            if lat is None or lon is None:
                continue
            dist = _haversine_km(home_lat, home_lon, lat, lon)
            if dist > WANDERING_DIST_KM:
                events.append(
                    AnomalyEvent(
                        user_id=user_id,
                        event_type="wandering",
                        severity="high",
                        description=(
                            f"User detected {dist:.1f} km from home at "
                            f"{chunk_ts.strftime('%H:%M')} (night hours)."
                        ),
                        timestamp=now,
                        metadata={
                            "distance_km": round(dist, 2),
                            "location": loc,
                            "chunk_id": getattr(chunk, "id", ""),
                        },
                    )
                )
                break  # One alert per cycle
        return events

    async def _check_routine_deviation(
        self,
        user_id: str,
        routine: dict[str, Any],
        memory_chunks: list[Any],
        now: datetime,
    ) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        today_str = now.date().isoformat()

        for chunk in memory_chunks:
            chunk_ts: datetime = getattr(chunk, "timestamp", now)
            if today_str not in chunk_ts.isoformat():
                continue
            chunk_hour = chunk_ts.hour + chunk_ts.minute / 60.0
            tags: list[str] = getattr(chunk, "tags", []) or []

            for event_type in ("wake", "sleep"):
                if event_type not in tags:
                    continue
                window: list[float] = routine.get(f"{event_type}_time", [0.0, 23.99])
                lo, hi = window
                if lo <= chunk_hour <= hi:
                    continue
                dev = min(abs(chunk_hour - lo), abs(chunk_hour - hi))
                if dev >= ROUTINE_DEVIATION_HOURS:
                    severity = "high" if dev >= 4 else "medium"
                    events.append(
                        AnomalyEvent(
                            user_id=user_id,
                            event_type="routine_deviation",
                            severity=severity,
                            description=(
                                f"{event_type.capitalize()} at "
                                f"{chunk_ts.strftime('%H:%M')} — expected "
                                f"{int(lo):02d}:00–{int(hi):02d}:59 "
                                f"(deviation {dev:.1f} h)."
                            ),
                            timestamp=now,
                            metadata={
                                "sub_type": event_type,
                                "expected_window": window,
                                "actual_hour": round(chunk_hour, 2),
                                "deviation_hours": round(dev, 2),
                            },
                        )
                    )
        return events

    async def _check_silence(
        self,
        user_id: str,
        routine: dict[str, Any],
        memory_chunks: list[Any],
        now: datetime,
    ) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        now_hour = now.hour + now.minute / 60.0
        wake_start: float = routine.get("wake_time", [6.0, 8.0])[0]
        sleep_start: float = routine.get("sleep_time", [22.0, 23.0])[0]

        if not (wake_start <= now_hour < sleep_start):
            return events

        if not memory_chunks:
            waking_hours = now_hour - wake_start
            if waking_hours >= SILENCE_HOURS:
                events.append(
                    AnomalyEvent(
                        user_id=user_id,
                        event_type="silence",
                        severity="high",
                        description=(
                            f"No memory entries for {waking_hours:.1f} h during waking hours."
                        ),
                        timestamp=now,
                        metadata={"waking_hours_elapsed": round(waking_hours, 2)},
                    )
                )
            return events

        latest_ts: datetime = max(
            (
                (
                    getattr(c, "timestamp", now).replace(tzinfo=timezone.utc)
                    if getattr(c, "timestamp", now).tzinfo is None
                    else getattr(c, "timestamp", now)
                )
                for c in memory_chunks
            ),
            default=now,
        )
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        gap_hours = (now_aware - latest_ts).total_seconds() / 3600.0

        if gap_hours >= SILENCE_HOURS:
            events.append(
                AnomalyEvent(
                    user_id=user_id,
                    event_type="silence",
                    severity="medium" if gap_hours < 6 else "high",
                    description=(
                        f"No memory entries for {gap_hours:.1f} h. "
                        f"Last entry at {latest_ts.strftime('%H:%M')}."
                    ),
                    timestamp=now,
                    metadata={
                        "gap_hours": round(gap_hours, 2),
                        "last_entry": latest_ts.isoformat(),
                    },
                )
            )
        return events

    # ------------------------------------------------------------------
    # Unified polling check
    # ------------------------------------------------------------------

    async def check_all(
        self,
        user_id: str,
        routine_tracker: Any | None = None,
        episodic_memory: Any | None = None,
        home_location: dict[str, float] | None = None,
    ) -> list[AnomalyEvent]:
        """
        Run all polling checks; return newly recorded anomalies.

        Deduplicates against anomalies already active for today.
        """
        now = datetime.now(tz=timezone.utc)

        routine: dict[str, Any] = {}
        if routine_tracker is not None:
            try:
                routine = await routine_tracker.get_routine(user_id)
            except Exception as exc:
                log.warning("detector.routine_load_failed", error=str(exc))

        memory_chunks: list[Any] = []
        if episodic_memory is not None:
            try:
                all_recent = await episodic_memory.get_recent(user_id, limit=200)
                cutoff = now - timedelta(hours=12)
                memory_chunks = [
                    c
                    for c in all_recent
                    if (
                        getattr(c, "timestamp", now).replace(tzinfo=timezone.utc)
                        if getattr(c, "timestamp", now).tzinfo is None
                        else getattr(c, "timestamp", now)
                    )
                    >= cutoff
                ]
            except Exception as exc:
                log.warning("detector.memory_load_failed", error=str(exc))

        # Already-active signatures for today → skip duplicates
        today_str = now.date().isoformat()
        active_sigs: set[str] = {
            f"{e.event_type}:{today_str}"
            for e in self._active.get(user_id, [])
        }

        async def _safe(coro: Any) -> list[AnomalyEvent]:
            try:
                return await coro
            except Exception as exc:
                log.error("detector.check_error", error=str(exc))
                return []

        batches = await asyncio.gather(
            _safe(self._check_meal_skip(user_id, routine, memory_chunks, now)),
            _safe(self._check_med_skip(user_id, routine, memory_chunks, now)),
            _safe(self._check_wandering(user_id, memory_chunks, now, home_location)),
            _safe(self._check_routine_deviation(user_id, routine, memory_chunks, now)),
            _safe(self._check_silence(user_id, routine, memory_chunks, now)),
        )

        new_anomalies: list[AnomalyEvent] = []
        for batch in batches:
            for evt in batch:
                sig = f"{evt.event_type}:{today_str}"
                if sig not in active_sigs:
                    active_sigs.add(sig)
                    await self._record(user_id, evt)
                    new_anomalies.append(evt)

        log.info(
            "detector.check_all",
            user_id=user_id,
            new_anomalies=len(new_anomalies),
        )
        return new_anomalies

    # ------------------------------------------------------------------
    # Active anomaly management
    # ------------------------------------------------------------------

    async def get_active(self, user_id: str) -> list[AnomalyEvent]:
        """Return all unresolved anomalies for a user."""
        return list(self._active.get(user_id, []))

    async def resolve(self, anomaly_id: str) -> bool:
        """Mark an anomaly resolved; returns False if not found."""
        async with self._lock:
            evt = self._registry.get(anomaly_id)
            if evt is None:
                return False
            user_id = evt.user_id
            self._active[user_id] = [
                e for e in self._active.get(user_id, [])
                if self._event_id(e) != anomaly_id
            ]
        log.info("detector.resolved", anomaly_id=anomaly_id)
        return True

    async def get_severity_summary(self, user_id: str) -> dict[str, Any]:
        """Return counts of active anomalies by severity."""
        active = self._active.get(user_id, [])
        counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        for evt in active:
            key = evt.severity if evt.severity in counts else "low"
            counts[key] += 1
        return {
            **counts,
            "last_checked": datetime.now(tz=timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Background monitoring
    # ------------------------------------------------------------------

    async def start_monitoring(
        self,
        user_id: str,
        interval_seconds: int = 300,
        webhook_url: str | None = None,
        routine_tracker: Any | None = None,
        episodic_memory: Any | None = None,
        home_location: dict[str, float] | None = None,
    ) -> None:
        """
        Launch a background asyncio task that calls check_all every
        interval_seconds and POSTs to webhook_url on high-severity finds.
        """
        if user_id in self._monitor_tasks:
            existing = self._monitor_tasks[user_id]
            if not existing.done():
                log.info("detector.monitor_already_running", user_id=user_id)
                return
            del self._monitor_tasks[user_id]

        async def _loop() -> None:
            log.info(
                "detector.monitor_started",
                user_id=user_id,
                interval_seconds=interval_seconds,
            )
            while True:
                try:
                    new_anomalies = await self.check_all(
                        user_id=user_id,
                        routine_tracker=routine_tracker,
                        episodic_memory=episodic_memory,
                        home_location=home_location,
                    )
                    high = [a for a in new_anomalies if a.severity == "high"]
                    if high and webhook_url:
                        await _send_webhook(webhook_url, user_id, high)
                except asyncio.CancelledError:
                    log.info("detector.monitor_stopped", user_id=user_id)
                    raise
                except Exception as exc:
                    log.error("detector.monitor_error", user_id=user_id, error=str(exc))
                await asyncio.sleep(interval_seconds)

        task = asyncio.create_task(_loop(), name=f"monitor_{user_id}")
        self._monitor_tasks[user_id] = task


async def _send_webhook(
    webhook_url: str,
    user_id: str,
    anomalies: list[AnomalyEvent],
) -> None:
    payload = {
        "user_id": user_id,
        "alert_count": len(anomalies),
        "anomalies": [
            {
                "event_type": a.event_type,
                "severity": a.severity,
                "description": a.description,
                "timestamp": a.timestamp.isoformat(),
                "metadata": a.metadata,
            }
            for a in anomalies
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(webhook_url, json=payload)
            log.info("detector.webhook_sent", status=r.status_code, count=len(anomalies))
    except Exception as exc:
        log.warning("detector.webhook_failed", error=str(exc))


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

anomaly_router = APIRouter(prefix="/anomaly", tags=["anomaly"])


def _get_detector(request: Request) -> AnomalyDetector:
    return request.app.state.anomaly_detector  # type: ignore[no-any-return]


def _get_routine_tracker(request: Request) -> Any:
    return getattr(request.app.state, "routine_tracker", None)


def _get_episodic_memory(request: Request) -> Any:
    return getattr(request.app.state, "episodic_memory", None)


class EvaluateRequest(BaseModel):
    context: dict[str, Any]


class StartMonitoringRequest(BaseModel):
    webhook_url: str | None = None
    interval: int = 300
    home_location: dict[str, float] | None = None


@anomaly_router.post("/evaluate", response_model=list[AnomalyEvent])
async def evaluate_context(
    body: EvaluateRequest,
    request: Request,
) -> list[AnomalyEvent]:
    """Run registered rules against an event context dict."""
    detector: AnomalyDetector = _get_detector(request)
    return await detector.evaluate(body.context)


@anomaly_router.get("/recent/{user_id}", response_model=list[AnomalyEvent])
async def recent_anomalies(
    user_id: str,
    limit: int = 50,
    severity: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> list[AnomalyEvent]:
    """Return recent anomalies from the bounded log."""
    detector: AnomalyDetector = _get_detector(request)
    return await detector.get_recent(user_id=user_id, limit=limit, severity=severity)


@anomaly_router.get("/{user_id}/status")
async def severity_summary(user_id: str, request: Request) -> dict[str, Any]:
    """Return active anomaly severity summary."""
    return await _get_detector(request).get_severity_summary(user_id)


@anomaly_router.get("/{user_id}/active")
async def active_anomalies(user_id: str, request: Request) -> list[dict[str, Any]]:
    """Return all unresolved anomalies for a user."""
    detector: AnomalyDetector = _get_detector(request)
    anomalies = await detector.get_active(user_id)
    return [
        {
            "id": detector._event_id(a),
            "event_type": a.event_type,
            "severity": a.severity,
            "description": a.description,
            "timestamp": a.timestamp.isoformat(),
            "metadata": a.metadata,
        }
        for a in anomalies
    ]


@anomaly_router.post("/{anomaly_id}/resolve")
async def resolve_anomaly(anomaly_id: str, request: Request) -> dict[str, Any]:
    """Mark anomaly resolved."""
    detector: AnomalyDetector = _get_detector(request)
    success = await detector.resolve(anomaly_id)
    if not success:
        raise HTTPException(status_code=404, detail="Anomaly not found or already resolved")
    return {"resolved": True, "anomaly_id": anomaly_id}


@anomaly_router.post("/{user_id}/start-monitoring")
async def start_monitoring(
    user_id: str,
    body: StartMonitoringRequest,
    request: Request,
) -> dict[str, Any]:
    """Start background anomaly monitoring for a user."""
    detector: AnomalyDetector = _get_detector(request)
    await detector.start_monitoring(
        user_id=user_id,
        interval_seconds=body.interval,
        webhook_url=body.webhook_url,
        routine_tracker=_get_routine_tracker(request),
        episodic_memory=_get_episodic_memory(request),
        home_location=body.home_location,
    )
    return {
        "status": "monitoring_started",
        "user_id": user_id,
        "interval_seconds": body.interval,
    }
