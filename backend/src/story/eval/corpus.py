"""Corpus loading: read a story-v2 SQLite event store into plain dataclasses.

The harness stands outside the engine: it opens the database directly with
``sqlite3`` and parses the event envelopes as JSON dictionaries, so no
runtime module can leak into the offline path.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Failure categories (consensus doc §二).  Codes ①-⑤ mirror the doc.
CATEGORIES = {
    "choice_continuation_miss": "① 选项后果未兑现",
    "scene_time_regression": "② 场景跳变(时间倒流)",
    "quote_style_break": "③ 语气断裂(排版漂移)",
    "segment_repetition": "④ 整段复读",
    "detail_contradiction": "⑤ 细节自相矛盾",
}


@dataclass(frozen=True)
class Block:
    index: int
    kind: str
    text: str
    character_id: str | None


@dataclass(frozen=True)
class Scene:
    sequence: int
    scene_id: str
    terminal: str
    present_character_ids: tuple[str, ...]
    blocks: tuple[Block, ...]
    occurred_at: str

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)

    @property
    def block_count(self) -> int:
        return len(self.blocks)


@dataclass(frozen=True)
class Decision:
    sequence: int
    decision_id: str
    choices: tuple[dict, ...]
    occurred_at: str

    def label_of(self, option_id: str) -> str:
        for choice in self.choices:
            if choice.get("id") == option_id:
                return choice.get("label", "")
        return ""


@dataclass(frozen=True)
class Selection:
    sequence: int
    decision_id: str
    option_id: str
    action_id: str
    intent: str
    target_character_id: str | None
    label: str
    occurred_at: str


@dataclass(frozen=True)
class Segment:
    """Scenes committed between one decision point and the next."""

    index: int  # 1-based, in reading order
    scenes: tuple[Scene, ...]

    @property
    def sequences(self) -> tuple[int, ...]:
        return tuple(scene.sequence for scene in self.scenes)

    @property
    def block_count(self) -> int:
        return sum(scene.block_count for scene in self.scenes)

    @property
    def text(self) -> str:
        return "\n".join(scene.text for scene in self.scenes)

    def narration_text(self) -> str:
        return "\n".join(
            block.text
            for scene in self.scenes
            for block in scene.blocks
            if block.kind != "dialogue"
        )

    def dialogue_lines(self, character_id: str) -> list[str]:
        return [
            block.text
            for scene in self.scenes
            for block in scene.blocks
            if block.kind == "dialogue" and block.character_id == character_id
        ]


@dataclass(frozen=True)
class SessionCorpus:
    session_id: str
    pack_id: str
    created_at: str
    scenes: tuple[Scene, ...]
    decisions: tuple[Decision, ...]
    selections: tuple[Selection, ...]
    segments: tuple[Segment, ...]

    @property
    def block_count(self) -> int:
        return sum(scene.block_count for scene in self.scenes)

    def scene_at(self, sequence: int) -> Scene | None:
        return next((scene for scene in self.scenes if scene.sequence == sequence), None)

    def scenes_between(self, after: int, before: int) -> tuple[Scene, ...]:
        return tuple(
            scene for scene in self.scenes if after < scene.sequence < before
        )


@dataclass(frozen=True)
class Corpus:
    sessions: tuple[SessionCorpus, ...]

    @property
    def block_count(self) -> int:
        return sum(session.block_count for session in self.sessions)


def parse_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _segments(
    scenes: tuple[Scene, ...], decisions: tuple[Decision, ...]
) -> tuple[Segment, ...]:
    """Group scenes into segments split at each decision presentation.

    Each decision ends the segment it was presented with; trailing scenes
    after the last decision (the player stopped before choosing) form a
    final segment — that text was still presented.
    """
    decision_sequences = sorted(decision.sequence for decision in decisions)
    groups: list[tuple[Scene, ...]] = []
    current: list[Scene] = []
    for position, scene in enumerate(scenes):
        current.append(scene)
        next_sequence = scenes[position + 1].sequence if position + 1 < len(scenes) else None
        closes_segment = any(
            scene.sequence < decision_sequence
            and (next_sequence is None or decision_sequence < next_sequence)
            for decision_sequence in decision_sequences
        )
        if closes_segment:
            groups.append(tuple(current))
            current = []
    if current:
        groups.append(tuple(current))
    return tuple(Segment(index=i, scenes=group) for i, group in enumerate(groups, start=1))


def _load_session(db: sqlite3.Connection, row: tuple[str, str, str]) -> SessionCorpus:
    session_id, pack_id, created_at = row
    scenes: list[Scene] = []
    decisions: list[Decision] = []
    raw_selections: list[tuple[int, dict, str]] = []
    for sequence, event_json in db.execute(
        "SELECT sequence, event_json FROM story_events WHERE session_id = ? ORDER BY sequence",
        (session_id,),
    ):
        envelope = json.loads(event_json)
        event = envelope.get("event", {})
        occurred_at = envelope.get("occurred_at", "")
        kind = event.get("type")
        if kind == "scene_committed":
            blocks = tuple(
                Block(
                    index=i,
                    kind=block.get("kind", "narration"),
                    text=block.get("text", ""),
                    character_id=block.get("character_id"),
                )
                for i, block in enumerate(event.get("blocks", ()))
            )
            scenes.append(
                Scene(
                    sequence=sequence,
                    scene_id=event.get("scene_id", ""),
                    terminal=event.get("terminal", "continue"),
                    present_character_ids=tuple(event.get("present_character_ids", ())),
                    blocks=blocks,
                    occurred_at=occurred_at,
                )
            )
        elif kind == "decision_presented":
            decisions.append(
                Decision(
                    sequence=sequence,
                    decision_id=event.get("decision_id", ""),
                    choices=tuple(event.get("choices", ())),
                    occurred_at=occurred_at,
                )
            )
        elif kind == "player_action_selected":
            raw_selections.append((sequence, event, occurred_at))

    decisions_by_id = {decision.decision_id: decision for decision in decisions}
    selections = tuple(
        Selection(
            sequence=sequence,
            decision_id=event.get("decision_id", ""),
            option_id=event.get("option_id", ""),
            action_id=event.get("action_id", ""),
            intent=event.get("intent", ""),
            target_character_id=event.get("target_character_id"),
            label=decisions_by_id.get(event.get("decision_id", ""), Decision(0, "", (), ""))
            .label_of(event.get("option_id", "")),
            occurred_at=occurred_at,
        )
        for sequence, event, occurred_at in raw_selections
    )
    return SessionCorpus(
        session_id=session_id,
        pack_id=pack_id,
        created_at=created_at,
        scenes=tuple(scenes),
        decisions=tuple(decisions),
        selections=selections,
        segments=_segments(tuple(scenes), tuple(decisions)),
    )


def load_corpus(database: Path | str, session_ids: list[str] | None = None) -> Corpus:
    """Load sessions from a story-v2 event store, ordered by creation time."""
    db = sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True)
    try:
        rows = list(
            db.execute(
                "SELECT session_id, pack_id, created_at FROM story_sessions ORDER BY created_at"
            )
        )
        if session_ids:
            wanted = set(session_ids)
            rows = [row for row in rows if row[0] in wanted]
            missing = wanted - {row[0] for row in rows}
            if missing:
                raise LookupError(f"session not found: {', '.join(sorted(missing))}")
        sessions = tuple(_load_session(db, row) for row in rows)
    finally:
        db.close()
    return Corpus(sessions=sessions)
