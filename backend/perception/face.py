"""
Face recognition module for Sahayak.

Uses InsightFace for face detection and 512-dimensional ArcFace embedding
extraction.  Person embeddings are persisted in LanceDB so the caregiver
can register known faces once and the system recognises them across sessions.
"""
from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa
import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image

from config import settings
from schemas import Person

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# LanceDB schema  — 512-dim ArcFace embeddings
# ---------------------------------------------------------------------------
_FACE_SCHEMA = pa.schema(
    [
        pa.field("person_id", pa.string()),
        pa.field("user_id", pa.string()),
        pa.field("name", pa.string()),
        pa.field("relationship", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), 512)),
        pa.field("last_seen", pa.string()),        # ISO-8601 or ""
        pa.field("interaction_count", pa.int64()),
        pa.field("notes", pa.string()),
        pa.field("confirmed", pa.bool_()),
    ]
)


def _person_to_row(person: Person) -> dict[str, Any]:
    last_seen_str = person.last_seen.isoformat() if person.last_seen else ""
    return {
        "person_id": person.id,
        "user_id": person.user_id,
        "name": person.name,
        "relationship": person.relationship,
        "embedding": np.array(person.face_embedding, dtype=np.float32),
        "last_seen": last_seen_str,
        "interaction_count": person.interaction_count,
        "notes": person.notes,
        "confirmed": person.confirmed,
    }


def _row_to_person(row: dict[str, Any]) -> Person:
    last_seen: datetime | None = None
    ls_raw = row.get("last_seen", "")
    if ls_raw:
        try:
            last_seen = datetime.fromisoformat(ls_raw)
        except ValueError:
            pass
    return Person(
        id=row["person_id"],
        user_id=row["user_id"],
        name=row["name"],
        relationship=row["relationship"],
        face_embedding=list(row.get("embedding") or []),
        last_seen=last_seen,
        interaction_count=int(row.get("interaction_count", 0)),
        notes=row.get("notes", ""),
        confirmed=bool(row.get("confirmed", False)),
    )


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class FaceService:
    """
    Face registration and recognition service backed by InsightFace + LanceDB.

    Call ``await instance.initialize()`` before first use.
    All public methods are coroutines; heavy inference runs in a thread-pool
    executor to avoid blocking the event loop.
    """

    def __init__(self) -> None:
        self._app: Any = None          # insightface.app.FaceAnalysis
        self._db: lancedb.DBConnection | None = None
        self._table: lancedb.table.Table | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        log.info("face_service.init.start", model=settings.FACE_MODEL)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_init)
        log.info("face_service.init.done")

    def _sync_init(self) -> None:
        from insightface.app import FaceAnalysis  # type: ignore[import]

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._app = FaceAnalysis(
            name=settings.FACE_MODEL,
            root=str(settings.data_dir),
            providers=["CPUExecutionProvider"],
        )
        self._app.prepare(ctx_id=0, det_size=(640, 640))

        self._db = lancedb.connect(settings.LANCEDB_PATH)
        if "faces" not in self._db.table_names():
            empty = pa.table(
                {f.name: pa.array([], type=f.type) for f in _FACE_SCHEMA},
                schema=_FACE_SCHEMA,
            )
            self._table = self._db.create_table("faces", data=empty)
        else:
            self._table = self._db.open_table("faces")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pil_to_bgr(self, image_bytes: bytes) -> np.ndarray:
        """Decode image bytes → BGR ndarray expected by InsightFace."""
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        rgb = np.array(img)
        return rgb[:, :, ::-1].copy()

    def _detect_sync(self, image_bytes: bytes) -> list[Any]:
        bgr = self._pil_to_bgr(image_bytes)
        return self._app.get(bgr)  # type: ignore[union-attr]

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register_face(
        self,
        name: str,
        relationship: str,
        user_id: str,
        image_bytes: bytes,
    ) -> Person:
        """
        Detect the primary face in *image_bytes*, extract its 512-dim ArcFace
        embedding, and persist a new ``Person`` record in LanceDB.

        Raises ``HTTPException(422)`` if no face is detected.
        """
        loop = asyncio.get_running_loop()
        faces: list[Any] = await loop.run_in_executor(
            None, self._detect_sync, image_bytes
        )
        if not faces:
            raise HTTPException(
                status_code=422,
                detail="No face detected in the provided image.",
            )

        # Pick the largest detected face as the enrolment subject
        primary = max(
            faces,
            key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
        )
        emb: np.ndarray = primary.embedding.astype(np.float32)
        emb /= np.linalg.norm(emb)  # L2-normalise before storage

        person = Person(
            user_id=user_id,
            name=name,
            relationship=relationship,
            face_embedding=emb.tolist(),
            last_seen=datetime.now(timezone.utc),
            interaction_count=0,
            confirmed=False,
        )

        async with self._lock:
            def _store() -> None:
                assert self._table is not None
                try:
                    self._table.delete(f"person_id = '{person.id}'")
                except Exception:
                    pass
                self._table.add([_person_to_row(person)])

            await loop.run_in_executor(None, _store)

        log.info("face_service.registered", person_id=person.id, name=name)
        return person

    async def recognize_faces(
        self,
        image_bytes: bytes,
        user_id: str,
        threshold: float = 0.5,
    ) -> list[tuple[Person, float]]:
        """
        Detect all faces in *image_bytes* and match each against stored persons
        for *user_id*.

        Returns ``[(Person, confidence), …]`` for matches that exceed
        *threshold*.  Updates ``last_seen`` and ``interaction_count``
        for every matched person.
        """
        loop = asyncio.get_running_loop()
        faces: list[Any] = await loop.run_in_executor(
            None, self._detect_sync, image_bytes
        )
        if not faces:
            return []

        # Load all stored persons for this user
        def _load_stored() -> list[dict[str, Any]]:
            assert self._table is not None
            return (
                self._table.search()
                .where(f"user_id = '{user_id}'", prefilter=True)
                .limit(10_000)
                .to_list()
            )

        stored: list[dict[str, Any]] = await loop.run_in_executor(None, _load_stored)
        if not stored:
            return []

        stored_embs = [
            np.array(r["embedding"], dtype=np.float32) for r in stored
        ]

        now_iso = datetime.now(timezone.utc).isoformat()
        results: list[tuple[Person, float]] = []

        for face in faces:
            query_emb = face.embedding.astype(np.float32)
            query_emb /= np.linalg.norm(query_emb)

            best_idx, best_sim = -1, -1.0
            for idx, stored_emb in enumerate(stored_embs):
                sim = self._cosine_similarity(query_emb, stored_emb)
                if sim > best_sim:
                    best_sim = sim
                    best_idx = idx

            if best_sim >= threshold and best_idx >= 0:
                row = stored[best_idx]
                person = _row_to_person(row)
                results.append((person, round(best_sim, 4)))

                # Persist updated last_seen + interaction_count
                pid = person.id
                new_count = person.interaction_count + 1

                async with self._lock:
                    def _update(
                        _pid: str = pid,
                        _count: int = new_count,
                        _now: str = now_iso,
                    ) -> None:
                        assert self._table is not None
                        rows = (
                            self._table.search()
                            .where(f"person_id = '{_pid}'", prefilter=True)
                            .limit(1)
                            .to_list()
                        )
                        if rows:
                            updated = dict(rows[0])
                            updated["last_seen"] = _now
                            updated["interaction_count"] = _count
                            self._table.delete(f"person_id = '{_pid}'")
                            self._table.add([updated])

                    await loop.run_in_executor(None, _update)

        return results

    async def confirm_face(self, person_id: str) -> Person:
        """
        Mark a person as caregiver-confirmed.

        Raises ``HTTPException(404)`` if the person does not exist.
        """
        loop = asyncio.get_running_loop()

        def _load() -> dict[str, Any] | None:
            assert self._table is not None
            rows = (
                self._table.search()
                .where(f"person_id = '{person_id}'", prefilter=True)
                .limit(1)
                .to_list()
            )
            return rows[0] if rows else None

        row = await loop.run_in_executor(None, _load)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Person '{person_id}' not found."
            )

        async with self._lock:
            def _confirm() -> None:
                assert self._table is not None
                updated = dict(row)
                updated["confirmed"] = True
                self._table.delete(f"person_id = '{person_id}'")
                self._table.add([updated])

            await loop.run_in_executor(None, _confirm)

        person = _row_to_person(dict(row))
        person.confirmed = True
        log.info("face_service.confirmed", person_id=person_id)
        return person

    async def list_persons(self, user_id: str) -> list[Person]:
        """Return all registered persons for *user_id*."""
        loop = asyncio.get_running_loop()

        def _list() -> list[dict[str, Any]]:
            assert self._table is not None
            return (
                self._table.search()
                .where(f"user_id = '{user_id}'", prefilter=True)
                .limit(10_000)
                .to_list()
            )

        rows: list[dict[str, Any]] = await loop.run_in_executor(None, _list)
        return [_row_to_person(r) for r in rows]


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/face", tags=["face"])

_service: FaceService | None = None
_init_lock = asyncio.Lock()


async def _get_service() -> FaceService:
    global _service
    if _service is None:
        async with _init_lock:
            if _service is None:
                svc = FaceService()
                await svc.initialize()
                _service = svc
    return _service


@router.post("/register", response_model=Person)
async def api_register_face(
    name: str = Form(...),
    relationship: str = Form(...),
    user_id: str = Form(...),
    image_file: UploadFile = File(...),
) -> Person:
    """Enrol a new face; embedding is extracted server-side."""
    svc = await _get_service()
    image_bytes = await image_file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is empty.")
    return await svc.register_face(name, relationship, user_id, image_bytes)


@router.post("/recognize")
async def api_recognize_faces(
    user_id: str = Form(...),
    image_file: UploadFile = File(...),
    threshold: float = Form(default=0.5),
) -> list[dict[str, Any]]:
    """
    Recognise all faces in an uploaded image.

    Returns ``[{"person": {…}, "confidence": float}, …]``.
    """
    svc = await _get_service()
    image_bytes = await image_file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is empty.")
    matches = await svc.recognize_faces(image_bytes, user_id, threshold)
    return [
        {"person": person.model_dump(), "confidence": confidence}
        for person, confidence in matches
    ]


@router.post("/{person_id}/confirm", response_model=Person)
async def api_confirm_face(person_id: str) -> Person:
    """Mark a registered person as caregiver-confirmed."""
    svc = await _get_service()
    return await svc.confirm_face(person_id)


@router.get("/{user_id}/persons", response_model=list[Person])
async def api_list_persons(user_id: str) -> list[Person]:
    """List all registered persons for a user."""
    svc = await _get_service()
    return await svc.list_persons(user_id)
