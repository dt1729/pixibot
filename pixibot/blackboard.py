"""Pixibot blackboard: an append-only SQLite event log (DESIGN.md §13).

The blackboard is the single source of shared state. Agents coordinate by
writing *addressed* events (``from_agent`` -> ``to_agent``) and reading their
inbox. The ``events`` table is immutable/append-only — the Observer's history is
sacred — so mutable state (delivery cursors, the agent registry) lives in
separate tables.

Thread-safe: the connection is opened with ``check_same_thread=False`` and every
method serializes on an ``RLock`` so a background build thread and the UI thread
can share one Blackboard.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

# ── Event kinds ──────────────────────────────────────────────────────────────
KIND_ARTIFACT = "artifact"    # a work product written to a section
KIND_MESSAGE = "message"      # a directed agent -> agent message
KIND_DIRECTIVE = "directive"  # a user -> agent instruction
KIND_CONTROL = "control"      # lifecycle / budget / checkpoint signal
VALID_KINDS = {KIND_ARTIFACT, KIND_MESSAGE, KIND_DIRECTIVE, KIND_CONTROL}

BROADCAST = "*"  # to_agent value that fans out to every agent


class ScopeError(PermissionError):
    """Raised when an agent writes an artifact outside its declared scope."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL,
    ts          TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    from_agent  TEXT    NOT NULL,
    to_agent    TEXT,
    section     TEXT,
    payload     TEXT    NOT NULL,
    in_reply_to INTEGER REFERENCES events(id),
    urgent      INTEGER NOT NULL DEFAULT 0,
    meta        TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox   ON events(to_agent, id);
CREATE INDEX IF NOT EXISTS idx_section ON events(section, id);
CREATE INDEX IF NOT EXISTS idx_run     ON events(run_id, id);

CREATE TABLE IF NOT EXISTS cursors (
    agent_id           TEXT PRIMARY KEY,
    last_seen_event_id INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    role     TEXT,
    depth    TEXT,
    model    TEXT,
    scope    TEXT,
    state    TEXT,
    budget   TEXT
);
"""


@dataclass
class Event:
    """One immutable row from the event log."""

    id: int
    run_id: str
    ts: str
    kind: str
    from_agent: str
    to_agent: Optional[str]
    section: Optional[str]
    payload: str
    in_reply_to: Optional[int]
    urgent: bool
    meta: Optional[dict]

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "Event":
        return cls(
            id=row["id"], run_id=row["run_id"], ts=row["ts"], kind=row["kind"],
            from_agent=row["from_agent"], to_agent=row["to_agent"],
            section=row["section"], payload=row["payload"],
            in_reply_to=row["in_reply_to"], urgent=bool(row["urgent"]),
            meta=json.loads(row["meta"]) if row["meta"] else None,
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


class Blackboard:
    """A run-scoped, thread-safe view over the SQLite event log."""

    def __init__(self, db_path: str, run_id: str = "default"):
        self.run_id = run_id
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL;")   # safe concurrent readers/writers
        self._db.execute("PRAGMA foreign_keys=ON;")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── agent registry (mutable) ────────────────────────────────────────────
    def register_agent(self, agent_id: str, *, role: Optional[str] = None,
                       depth: Optional[str] = None, model: Optional[str] = None,
                       scope: Optional[str] = None, state: str = "SPAWNED",
                       budget: Optional[dict] = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO agents(agent_id, role, depth, model, scope, state, budget) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(agent_id) DO UPDATE SET "
                "  role=excluded.role, depth=excluded.depth, model=excluded.model, "
                "  scope=excluded.scope, state=excluded.state, budget=excluded.budget",
                (agent_id, role, depth, model, scope, state,
                 json.dumps(budget) if budget is not None else None),
            )
            self._db.execute(
                "INSERT OR IGNORE INTO cursors(agent_id, last_seen_event_id) VALUES(?, 0)",
                (agent_id,),
            )
            self._db.commit()

    def set_state(self, agent_id: str, state: str) -> None:
        with self._lock:
            self._db.execute("UPDATE agents SET state=? WHERE agent_id=?", (state, agent_id))
            self._db.commit()

    def get_agent(self, agent_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return None
        agent = dict(row)
        if agent.get("budget"):
            agent["budget"] = json.loads(agent["budget"])
        return agent

    def list_agents(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT agent_id, role, depth, model, scope, state FROM agents ORDER BY agent_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_event_id(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(id), 0) AS m FROM events WHERE run_id=?", (self.run_id,)
            ).fetchone()
        return int(row["m"])

    # ── writing (append-only) ───────────────────────────────────────────────
    def send(self, from_agent: str, payload: Any, *, to: Optional[str] = None,
             kind: str = KIND_MESSAGE, section: Optional[str] = None,
             in_reply_to: Optional[int] = None, urgent: bool = False,
             meta: Optional[dict] = None, enforce_scope: bool = True) -> int:
        """Append one event; return its id. Artifact writes require a ``section``
        and are checked against the sender's ``scope`` (scope-as-write-lock)."""
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown kind: {kind!r}")
        if kind == KIND_ARTIFACT:
            if section is None:
                raise ValueError("artifact events require a section")
            if enforce_scope:
                self._check_scope(from_agent, section)
        if not isinstance(payload, str):
            payload = json.dumps(payload)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(run_id, ts, kind, from_agent, to_agent, section, "
                "payload, in_reply_to, urgent, meta) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, _now(), kind, from_agent, to, section, payload,
                 in_reply_to, 1 if urgent else 0,
                 json.dumps(meta) if meta is not None else None),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def _check_scope(self, agent_id: str, section: str) -> None:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ScopeError(f"unknown agent {agent_id!r} cannot write {section!r}")
        scope = agent.get("scope")
        if not scope:
            return
        if section == scope or section.startswith(scope.rstrip("/") + "/"):
            return
        raise ScopeError(f"agent {agent_id!r} scope {scope!r} may not write section {section!r}")

    # ── reading ─────────────────────────────────────────────────────────────
    def poll_inbox(self, agent_id: str, *, advance: bool = True) -> list[Event]:
        with self._lock:
            row = self._db.execute(
                "SELECT last_seen_event_id FROM cursors WHERE agent_id=?", (agent_id,)
            ).fetchone()
            last = row["last_seen_event_id"] if row else 0
            rows = self._db.execute(
                "SELECT * FROM events WHERE (to_agent=? OR to_agent=?) AND id>? AND run_id=? "
                "ORDER BY id",
                (agent_id, BROADCAST, last, self.run_id),
            ).fetchall()
            events = [Event._from_row(r) for r in rows]
            if advance and events:
                self._db.execute(
                    "INSERT INTO cursors(agent_id, last_seen_event_id) VALUES(?,?) "
                    "ON CONFLICT(agent_id) DO UPDATE SET "
                    "  last_seen_event_id=excluded.last_seen_event_id",
                    (agent_id, events[-1].id),
                )
                self._db.commit()
        return events

    def read_section(self, section: str) -> Optional[Event]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM events WHERE section=? AND kind=? AND run_id=? "
                "ORDER BY id DESC LIMIT 1",
                (section, KIND_ARTIFACT, self.run_id),
            ).fetchone()
        return Event._from_row(row) if row else None

    def current_state(self) -> dict[str, Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE kind=? AND run_id=? ORDER BY id",
                (KIND_ARTIFACT, self.run_id),
            ).fetchall()
        state: dict[str, Event] = {}
        for r in rows:
            e = Event._from_row(r)
            if e.section is not None:
                state[e.section] = e
        return state

    def history(self, *, to_agent: Optional[str] = None, from_agent: Optional[str] = None,
                kind: Optional[str] = None) -> list[Event]:
        query = "SELECT * FROM events WHERE run_id=?"
        args: list[Any] = [self.run_id]
        if to_agent is not None:
            query += " AND to_agent=?"
            args.append(to_agent)
        if from_agent is not None:
            query += " AND from_agent=?"
            args.append(from_agent)
        if kind is not None:
            query += " AND kind=?"
            args.append(kind)
        query += " ORDER BY id"
        with self._lock:
            rows = self._db.execute(query, args).fetchall()
        return [Event._from_row(r) for r in rows]
