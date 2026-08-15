"""Readable playthrough transcripts maintained beside the event stream.

The event store stays the single source of truth: the markdown file is a
rendering that can be rebuilt at any time (``TranscriptWriter.rebuild`` /
``python -m src.story.cli export-transcript``).  Option text at each
decision point comes from ``decision_presented`` events — ``scene_committed``
carries no choices in the segment engine, and idempotent replay receipts
never carry them either.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path

from src.story.state import EventEnvelope
from src.story.state.events import StoryEvent

logger = logging.getLogger(__name__)


def _render_block(block) -> list[str]:
    if block.character_id is not None:
        return [f"**{block.character_id}**：{block.text}"]
    return [block.text]


def _render_scene(event) -> list[str]:
    lines = [f"## {event.scene_id}", ""]
    if event.summary:
        lines.extend((f"*{event.summary}*", ""))
    lines.extend(text for block in event.blocks for text in (*_render_block(block), ""))
    return lines


def _render_decision(event) -> list[str]:
    lines = [f"### 抉择 · {event.decision_id}", ""]
    for index, choice in enumerate(event.choices, start=1):
        entry = f"{index}. {choice.label}"
        if choice.preview:
            entry = f"{entry} —— *{choice.preview}*"
        lines.append(entry)
    lines.append("")
    return lines


def _render_selection(event) -> list[str]:
    intent = event.intent.strip() or event.option_id
    return [f"> 已选：`{event.option_id}` —— {intent}", ""]


def _render_ending(event) -> list[str]:
    lines = [f"## 终章 · {event.title}", ""]
    lines.extend(text for block in event.blocks for text in (*_render_block(block), ""))
    return lines


def render_events(events: Iterable[StoryEvent]) -> str:
    """Render transcript-relevant story events in stream order.

    Deterministic and stateless — the same events in the same order always
    render byte-identically, so incremental appends and a full rebuild
    produce the same file.  Events with no reader-facing text (fact
    bookkeeping, relationship deltas, ...) are skipped.
    """
    renderers = {
        "scene_committed": _render_scene,
        "decision_presented": _render_decision,
        "player_action_selected": _render_selection,
        "ending_generated": _render_ending,
        "session_ended": lambda event: [f"—— 完（{event.ending_id}）——", ""],
    }
    lines: list[str] = []
    for event in events:
        renderer = renderers.get(event.type)
        if renderer is not None:
            lines.extend(renderer(event))
    if not lines:
        return ""
    # Every renderer ends with a blank line, so appending one batch's render
    # after another's reproduces the full-stream render byte for byte.
    return "\n".join(lines) + "\n"


def transcript_header(session_id: str) -> str:
    return f"# Playthrough · {session_id}\n\n"


class TranscriptWriter:
    """Appends committed events to ``{root}/{session_id}.md`` incrementally.

    Quitting mid-playthrough keeps everything committed so far.  A lost or
    truncated file is recoverable from the store (:meth:`rebuild` or the
    CLI export command); appending to a missing file starts a fresh header
    rather than failing the turn.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, session_id: str) -> Path:
        return self._root / f"{session_id}.md"

    def append_events(self, session_id: str, events: Iterable[StoryEvent]) -> None:
        """Append a freshly committed batch of story events."""
        chunk = render_events(events)
        if not chunk:
            return
        path = self.path_for(session_id)
        self._root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open("a", encoding="utf-8") as handle:
                handle.write(chunk)
        else:
            path.write_text(transcript_header(session_id) + chunk, encoding="utf-8")

    def rebuild(
        self,
        session_id: str,
        envelopes: Sequence[EventEnvelope],
        path: Path | None = None,
    ) -> Path:
        """Overwrite the file from the full committed stream.

        *path* overrides the per-session location (CLI export to an
        explicit ``--out``) without disturbing the incremental file.
        """
        text = transcript_header(session_id) + render_events(
            envelope.event for envelope in envelopes
        )
        target = path if path is not None else self.path_for(session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target
