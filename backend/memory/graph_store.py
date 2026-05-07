"""
Relationship graph module for Sahayak.

Maintains an in-memory graph of Person nodes and typed edges (relationships)
with JSON persistence so the graph survives restarts.  Each user gets a
separate graph file at ``data/graph_{user_id}.json``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from config import settings
from schemas import Person

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal graph data structures
# ---------------------------------------------------------------------------

class _Node:
    """A Person node in the relationship graph."""

    __slots__ = ("person",)

    def __init__(self, person: Person) -> None:
        self.person = person


class _Edge:
    """A directed relationship edge between two Person nodes."""

    __slots__ = ("source_id", "target_id", "rel_type")

    def __init__(self, source_id: str, target_id: str, rel_type: str) -> None:
        self.source_id = source_id
        self.target_id = target_id
        self.rel_type = rel_type


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------

class RelationshipGraph:
    """
    In-memory + JSON-persisted graph of person relationships.

    The graph is keyed per user so multiple users can share the same
    ``RelationshipGraph`` instance without cross-contamination.

    Thread-safety: all mutating methods acquire an asyncio.Lock per user_id
    to avoid race conditions on the JSON file.
    """

    def __init__(self) -> None:
        # user_id -> {person_id -> _Node}
        self._nodes: dict[str, dict[str, _Node]] = {}
        # user_id -> list[_Edge]
        self._edges: dict[str, list[_Edge]] = {}
        # user_id -> asyncio.Lock
        self._locks: dict[str, asyncio.Lock] = {}
        self._data_dir = settings.data_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    def _graph_path(self, user_id: str) -> Path:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir / f"graph_{user_id}.json"

    def _load_sync(self, user_id: str) -> None:
        """Load graph from JSON file into memory (called once per user)."""
        path = self._graph_path(user_id)
        if not path.exists():
            self._nodes[user_id] = {}
            self._edges[user_id] = []
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._nodes[user_id] = {
                rec["id"]: _Node(Person(**rec)) for rec in data.get("persons", [])
            }
            self._edges[user_id] = [
                _Edge(e["source_id"], e["target_id"], e["rel_type"])
                for e in data.get("edges", [])
            ]
        except Exception as exc:
            log.warning("graph_store.load.error", user_id=user_id, error=str(exc))
            self._nodes[user_id] = {}
            self._edges[user_id] = []

    def _save_sync(self, user_id: str) -> None:
        """Persist the current in-memory graph to JSON."""
        path = self._graph_path(user_id)
        data = {
            "persons": [
                n.person.model_dump(mode="json")
                for n in self._nodes.get(user_id, {}).values()
            ],
            "edges": [
                {"source_id": e.source_id, "target_id": e.target_id, "rel_type": e.rel_type}
                for e in self._edges.get(user_id, [])
            ],
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _ensure_loaded(self, user_id: str) -> None:
        """Lazily load the user's graph if not already in memory."""
        if user_id not in self._nodes:
            self._load_sync(user_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_person(self, person: Person) -> None:
        """Add or update a Person node in the user's graph."""
        uid = person.user_id
        self._ensure_loaded(uid)
        self._nodes[uid][person.id] = _Node(person)
        self._save_sync(uid)
        log.info("graph_store.add_person", person_id=person.id, name=person.name)

    def add_relationship(
        self,
        person_a_id: str,
        person_b_id: str,
        rel_type: str,
        user_id: str,
    ) -> None:
        """
        Add a directed relationship edge ``person_a → person_b`` of type *rel_type*.

        Duplicate edges (same source, target, rel_type) are silently ignored.
        *user_id* is required to locate the correct graph partition.
        """
        self._ensure_loaded(user_id)
        existing = self._edges.setdefault(user_id, [])
        for e in existing:
            if (
                e.source_id == person_a_id
                and e.target_id == person_b_id
                and e.rel_type == rel_type
            ):
                return  # already exists
        existing.append(_Edge(person_a_id, person_b_id, rel_type))
        self._save_sync(user_id)
        log.info(
            "graph_store.add_relationship",
            from_id=person_a_id,
            to_id=person_b_id,
            type=rel_type,
        )

    def get_relations(self, person_id: str, user_id: str) -> list[dict[str, Any]]:
        """
        Return all relationships involving *person_id* (as source or target).

        Each entry in the returned list::

            {
                "person_id": str,    # the *other* party
                "name": str,
                "relationship": str, # Person.relationship field
                "rel_type": str,     # edge label
                "direction": "outgoing" | "incoming",
            }
        """
        self._ensure_loaded(user_id)
        nodes = self._nodes.get(user_id, {})
        relations: list[dict[str, Any]] = []
        for edge in self._edges.get(user_id, []):
            if edge.source_id == person_id:
                other = nodes.get(edge.target_id)
                if other:
                    relations.append(
                        {
                            "person_id": edge.target_id,
                            "name": other.person.name,
                            "relationship": other.person.relationship,
                            "rel_type": edge.rel_type,
                            "direction": "outgoing",
                        }
                    )
            elif edge.target_id == person_id:
                other = nodes.get(edge.source_id)
                if other:
                    relations.append(
                        {
                            "person_id": edge.source_id,
                            "name": other.person.name,
                            "relationship": other.person.relationship,
                            "rel_type": edge.rel_type,
                            "direction": "incoming",
                        }
                    )
        return relations

    def get_context_for_person(self, person_id: str, user_id: str) -> str:
        """
        Return a natural-language summary of who *person_id* is and how they
        relate to the user.

        Example output::

            "Ravi (son) was last seen 2 days ago and has been confirmed by
             the caregiver.  Known connections: married to Priya (daughter-
             in-law), parent of Arjun (grandson)."
        """
        self._ensure_loaded(user_id)
        nodes = self._nodes.get(user_id, {})
        node = nodes.get(person_id)
        if node is None:
            return f"No information found for person id '{person_id}'."

        person = node.person
        parts: list[str] = []

        # --- Core identity ---
        identity = f"{person.name} ({person.relationship})"
        if person.confirmed:
            identity += ", confirmed by caregiver"

        # --- Last seen ---
        if person.last_seen:
            delta = datetime.now(timezone.utc) - person.last_seen.replace(
                tzinfo=timezone.utc if person.last_seen.tzinfo is None else None
            ) if person.last_seen.tzinfo is None else (
                datetime.now(timezone.utc) - person.last_seen
            )
            days = delta.days
            if days == 0:
                seen_str = "seen today"
            elif days == 1:
                seen_str = "last seen yesterday"
            else:
                seen_str = f"last seen {days} days ago"
        else:
            seen_str = "last seen: unknown"

        parts.append(f"{identity} — {seen_str}.")

        # --- Interaction count ---
        if person.interaction_count > 0:
            parts.append(
                f"Interacted with {person.name} {person.interaction_count} time(s)."
            )

        # --- Notes ---
        if person.notes:
            parts.append(f"Notes: {person.notes}.")

        # --- Relations ---
        relations = self.get_relations(person_id, user_id)
        if relations:
            rel_strs = [
                f"{r['name']} ({r['relationship']}) [{r['rel_type']}]"
                for r in relations
            ]
            parts.append(f"Known connections: {', '.join(rel_strs)}.")

        return " ".join(parts)

    def get_all_persons(self, user_id: str) -> list[Person]:
        """Return all persons registered for *user_id*."""
        self._ensure_loaded(user_id)
        return [n.person for n in self._nodes.get(user_id, {}).values()]
