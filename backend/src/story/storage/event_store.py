from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from src.story.script_pack import compile_source
from src.story.script_pack.models import CompiledScriptPack
from src.story.state.events import EventEnvelope, StoryEvent
from src.story.state.models import SessionState, utc_now
from src.story.state.reducer import apply_events


class StoryStoreError(RuntimeError):
    """Base persistence error."""


class SessionAlreadyExists(StoryStoreError):
    pass


class SessionNotFound(StoryStoreError):
    pass


class RevisionConflict(StoryStoreError):
    pass


@dataclass(frozen=True)
class CommandClaim:
    replay_json: str | None = None


class CommandInProgress(StoryStoreError):
    pass


class CommandRequestMismatch(StoryStoreError):
    pass


class StoryEventStore:
    def __init__(self, database_path: Path | str, snapshot_every: int = 20) -> None:
        if snapshot_every < 1:
            raise ValueError("snapshot_every must be positive")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_every = snapshot_every
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS story_sessions (
                    session_id TEXT PRIMARY KEY,
                    pack_id TEXT NOT NULL,
                    pack_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    snapshot_revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS script_pack_versions (
                    pack_hash TEXT PRIMARY KEY,
                    pack_id TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS story_events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, sequence),
                    FOREIGN KEY (session_id) REFERENCES story_sessions(session_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS story_command_receipts (
                    session_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    command_kind TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed')),
                    lease_expires_at TEXT,
                    result_json TEXT,
                    result_revision INTEGER,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (session_id, command_id),
                    FOREIGN KEY (session_id) REFERENCES story_sessions(session_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS story_turn_diagnostics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    command_kind TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK (outcome IN ('committed', 'failed')),
                    recorded_at TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES story_sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_turn_diagnostics_session
                    ON story_turn_diagnostics (session_id, id);
                """)

    def create_session(
        self,
        state: SessionState,
        *,
        pack: CompiledScriptPack | None = None,
    ) -> None:
        if state.revision != 0:
            raise StoryStoreError("new session state must have revision 0")
        if pack is not None and (
            pack.pack_hash != state.pack_hash or pack.source.identity.id != state.pack_id
        ):
            raise StoryStoreError("session pack version does not match the supplied pack")
        try:
            with self._connect() as connection:
                if pack is not None:
                    source_json = pack.source.model_dump_json()
                    existing = connection.execute(
                        "SELECT pack_id, source_json FROM script_pack_versions WHERE pack_hash = ?",
                        (pack.pack_hash,),
                    ).fetchone()
                    if existing is not None and (
                        existing["pack_id"] != pack.source.identity.id
                        or json.loads(existing["source_json"]) != json.loads(source_json)
                    ):
                        raise StoryStoreError("pack version hash is already bound differently")
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO script_pack_versions (
                            pack_hash, pack_id, source_json, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            pack.pack_hash,
                            pack.source.identity.id,
                            source_json,
                            utc_now().isoformat(),
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO story_sessions (
                        session_id, pack_id, pack_hash, revision, snapshot_revision,
                        snapshot_json, created_at
                    ) VALUES (?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        state.session_id,
                        state.pack_id,
                        state.pack_hash,
                        state.model_dump_json(),
                        state.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SessionAlreadyExists(state.session_id) from exc

    def load_pack_version(self, pack_hash: str) -> CompiledScriptPack:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source_json FROM script_pack_versions WHERE pack_hash = ?",
                (pack_hash,),
            ).fetchone()
        if row is None:
            raise StoryStoreError(f"script pack version not found: {pack_hash}")
        pack = compile_source(json.loads(row["source_json"]))
        if pack.pack_hash != pack_hash:
            raise StoryStoreError("stored script pack version hash does not match its content")
        return pack

    def _load_with_connection(
        self, connection: sqlite3.Connection, session_id: str
    ) -> tuple[SessionState, sqlite3.Row]:
        row = connection.execute(
            "SELECT * FROM story_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        state = SessionState.model_validate_json(row["snapshot_json"])
        event_rows = connection.execute(
            """
            SELECT event_json FROM story_events
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence
            """,
            (session_id, row["snapshot_revision"]),
        ).fetchall()
        envelopes = [EventEnvelope.model_validate_json(item["event_json"]) for item in event_rows]
        state = apply_events(state, envelopes)
        if state.revision != row["revision"]:
            raise StoryStoreError(
                f"session {session_id} revision mismatch: state={state.revision}, row={row['revision']}"
            )
        return state, row

    def load_session(self, session_id: str) -> SessionState:
        with self._connect() as connection:
            state, _ = self._load_with_connection(connection, session_id)
            return state

    def append(
        self,
        session_id: str,
        expected_revision: int,
        events: Iterable[StoryEvent],
    ) -> tuple[SessionState, tuple[EventEnvelope, ...]]:
        event_list = tuple(events)
        if not event_list:
            raise StoryStoreError("append requires at least one event")
        envelopes = tuple(
            EventEnvelope(
                session_id=session_id,
                sequence=expected_revision + index,
                event=event,
            )
            for index, event in enumerate(event_list, start=1)
        )
        return self.append_envelopes(session_id, expected_revision, envelopes)

    def append_envelopes(
        self,
        session_id: str,
        expected_revision: int,
        envelopes: Iterable[EventEnvelope],
    ) -> tuple[SessionState, tuple[EventEnvelope, ...]]:
        envelope_list = tuple(envelopes)
        if not envelope_list:
            raise StoryStoreError("append_envelopes requires at least one envelope")
        if any(envelope.session_id != session_id for envelope in envelope_list):
            raise StoryStoreError("event envelope session does not match target session")
        expected_sequences = tuple(
            range(expected_revision + 1, expected_revision + len(envelope_list) + 1)
        )
        if tuple(envelope.sequence for envelope in envelope_list) != expected_sequences:
            raise StoryStoreError("event envelope sequences must be contiguous")
        event_ids = tuple(envelope.event_id for envelope in envelope_list)
        if len(event_ids) != len(set(event_ids)):
            raise StoryStoreError("event envelope IDs must be unique")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            state, row = self._load_with_connection(connection, session_id)
            if state.revision != expected_revision:
                raise RevisionConflict(
                    f"session {session_id}: expected {expected_revision}, current {state.revision}"
                )
            placeholders = ", ".join("?" for _ in event_ids)
            existing_event_id = connection.execute(
                f"SELECT event_id FROM story_events WHERE event_id IN ({placeholders}) LIMIT 1",
                event_ids,
            ).fetchone()
            if existing_event_id is not None:
                raise StoryStoreError(
                    f"event envelope batch violates persistence uniqueness: "
                    f"{existing_event_id['event_id']} already exists"
                )
            updated = apply_events(state, envelope_list)
            connection.executemany(
                """
                INSERT INTO story_events (session_id, sequence, event_id, event_json)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        envelope.session_id,
                        envelope.sequence,
                        envelope.event_id,
                        envelope.model_dump_json(),
                    )
                    for envelope in envelope_list
                ],
            )
            snapshot_revision = row["snapshot_revision"]
            snapshot_json = row["snapshot_json"]
            if updated.revision - snapshot_revision >= self.snapshot_every:
                snapshot_revision = updated.revision
                snapshot_json = updated.model_dump_json()
            connection.execute(
                """
                UPDATE story_sessions
                SET revision = ?, snapshot_revision = ?, snapshot_json = ?
                WHERE session_id = ?
                """,
                (updated.revision, snapshot_revision, snapshot_json, session_id),
            )
            connection.commit()
            return updated, envelope_list
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise StoryStoreError("event envelope batch violates persistence uniqueness") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_command(
        self,
        session_id: str,
        command_id: str,
        command_kind: str,
        request_fingerprint: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> CommandClaim:
        now = now or utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM story_command_receipts
                WHERE session_id = ? AND command_id = ?
                """,
                (session_id, command_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO story_command_receipts (
                        session_id, command_id, command_kind, request_fingerprint,
                        status, lease_expires_at, created_at
                    ) VALUES (?, ?, ?, ?, 'in_progress', ?, ?)
                    """,
                    (
                        session_id,
                        command_id,
                        command_kind,
                        request_fingerprint,
                        (now + timedelta(seconds=lease_seconds)).isoformat(),
                        now.isoformat(),
                    ),
                )
                connection.commit()
                return CommandClaim()
            if row["command_kind"] != command_kind or (
                row["request_fingerprint"] != request_fingerprint
            ):
                raise CommandRequestMismatch(
                    f"command {session_id}/{command_id} was already requested differently"
                )
            if row["status"] == "completed":
                connection.commit()
                return CommandClaim(replay_json=row["result_json"])
            lease_expires_at = datetime.fromisoformat(row["lease_expires_at"])
            if now < lease_expires_at:
                raise CommandInProgress(f"command {session_id}/{command_id} is still in progress")
            connection.execute(
                """
                UPDATE story_command_receipts
                SET lease_expires_at = ?
                WHERE session_id = ? AND command_id = ?
                """,
                ((now + timedelta(seconds=lease_seconds)).isoformat(), session_id, command_id),
            )
            connection.commit()
            return CommandClaim()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release_command(
        self,
        session_id: str,
        command_id: str,
        command_kind: str,
        request_fingerprint: str,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                DELETE FROM story_command_receipts
                WHERE session_id = ? AND command_id = ?
                  AND command_kind = ? AND request_fingerprint = ?
                  AND status = 'in_progress'
                """,
                (session_id, command_id, command_kind, request_fingerprint),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def commit_command(
        self,
        session_id: str,
        command_id: str,
        command_kind: str,
        request_fingerprint: str,
        expected_revision: int,
        events: Iterable[StoryEvent],
        result_factory: Callable[[SessionState, tuple[EventEnvelope, ...]], str],
        *,
        now: datetime | None = None,
        event_ids: Iterable[str] | None = None,
    ) -> tuple[SessionState, tuple[EventEnvelope, ...], str]:
        """Atomically append ``events`` under a claimed command receipt.

        ``event_ids`` optionally pins the committed envelope IDs (must be
        unique and exactly one per event).  This lets semantic citations —
        e.g. CompletionEvaluated evidence chains — reference the actual
        committed IDs.  Without it, envelope IDs are generated randomly.
        """
        now = now or utc_now()
        event_list = tuple(events)
        if not event_list:
            raise StoryStoreError("commit_command requires at least one event")
        if event_ids is not None:
            provided = tuple(event_ids)
            if len(provided) != len(event_list):
                raise StoryStoreError("event_ids must match the event count")
            if len(set(provided)) != len(provided):
                raise StoryStoreError("event_ids must be unique")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            state, row = self._load_with_connection(connection, session_id)
            if state.revision != expected_revision:
                raise RevisionConflict(
                    f"session {session_id}: expected {expected_revision}, current {state.revision}"
                )
            receipt = connection.execute(
                """
                SELECT * FROM story_command_receipts
                WHERE session_id = ? AND command_id = ?
                """,
                (session_id, command_id),
            ).fetchone()
            if receipt is None:
                raise StoryStoreError("commit_command requires a claimed command receipt")
            if receipt["command_kind"] != command_kind or (
                receipt["request_fingerprint"] != request_fingerprint
            ):
                raise CommandRequestMismatch(
                    f"command {session_id}/{command_id} receipt does not match request"
                )
            if event_ids is None:
                envelopes = tuple(
                    EventEnvelope(
                        session_id=session_id,
                        sequence=state.revision + index,
                        event=event,
                    )
                    for index, event in enumerate(event_list, start=1)
                )
            else:
                envelopes = tuple(
                    EventEnvelope(
                        event_id=event_id,
                        session_id=session_id,
                        sequence=state.revision + index,
                        event=event,
                    )
                    for index, (event, event_id) in enumerate(
                        zip(event_list, provided, strict=True), start=1
                    )
                )
            updated = apply_events(state, envelopes)
            connection.executemany(
                """
                INSERT INTO story_events (session_id, sequence, event_id, event_json)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        envelope.session_id,
                        envelope.sequence,
                        envelope.event_id,
                        envelope.model_dump_json(),
                    )
                    for envelope in envelopes
                ],
            )
            snapshot_revision = row["snapshot_revision"]
            snapshot_json = row["snapshot_json"]
            if updated.revision - snapshot_revision >= self.snapshot_every:
                snapshot_revision = updated.revision
                snapshot_json = updated.model_dump_json()
            connection.execute(
                """
                UPDATE story_sessions
                SET revision = ?, snapshot_revision = ?, snapshot_json = ?
                WHERE session_id = ?
                """,
                (updated.revision, snapshot_revision, snapshot_json, session_id),
            )
            result_json = result_factory(updated, envelopes)
            connection.execute(
                """
                UPDATE story_command_receipts
                SET status = 'completed', result_json = ?, result_revision = ?,
                    lease_expires_at = NULL, completed_at = ?
                WHERE session_id = ? AND command_id = ?
                """,
                (result_json, updated.revision, now.isoformat(), session_id, command_id),
            )
            connection.commit()
            return updated, envelopes, result_json
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def append_turn_diagnostics(self, session_id: str, record: dict) -> None:
        """Persist one turn's diagnostics (stage timings, judge findings).

        Author/developer-side only: a dedicated table next to the event
        stream, deliberately outside it — every event in ``story_events``
        advances ``state.revision``, which players mirror with
        ``expected_revision``, so diagnostics must never shift the stream.
        """
        connection = self._connect()
        try:
            exists = connection.execute(
                "SELECT 1 FROM story_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if exists is None:
                raise SessionNotFound(session_id)
            connection.execute(
                """
                INSERT INTO story_turn_diagnostics
                    (session_id, command_id, command_kind, outcome, recorded_at, diagnostics_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    record["command_id"],
                    record["command_kind"],
                    record["outcome"],
                    utc_now().isoformat(),
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def load_turn_diagnostics(self, session_id: str) -> list[dict]:
        """Return a session's diagnostics records in recording order."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT diagnostics_json FROM story_turn_diagnostics
                WHERE session_id = ? ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        return [json.loads(row["diagnostics_json"]) for row in rows]

    def list_sessions(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id FROM story_sessions ORDER BY session_id"
            ).fetchall()
            return [row["session_id"] for row in rows]

    def event_count(self, session_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM story_events WHERE session_id = ?", (session_id,)
            ).fetchone()
            return int(row["count"])

    def load_events(self, session_id: str, after_sequence: int = 0) -> tuple[EventEnvelope, ...]:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM story_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if exists is None:
                raise SessionNotFound(session_id)
            rows = connection.execute(
                """
                SELECT event_json FROM story_events
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence
                """,
                (session_id, after_sequence),
            ).fetchall()
            return tuple(EventEnvelope.model_validate_json(row["event_json"]) for row in rows)
