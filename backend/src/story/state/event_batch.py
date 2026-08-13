from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any
from uuid import uuid4

from pydantic import Field

from src.story.state.events import EventEnvelope, StoryEvent
from src.story.state.models import FrozenModel


class EventReferenceError(ValueError):
    """A proposed event batch contains an invalid event reference."""


class ProposedEvent(FrozenModel):
    local_ref: str = Field(pattern=r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")
    event: StoryEvent


def _new_event_id() -> str:
    return str(uuid4())


def _resolve_reference(
    value: str,
    allocated: dict[str, str],
    committed_event_ids: set[str],
) -> str:
    if value in allocated:
        return allocated[value]
    if value in committed_event_ids:
        return value
    raise EventReferenceError(f"unknown event reference {value}")


def _resolve_reference_field(
    field_name: str,
    value: Any,
    allocated: dict[str, str],
    committed_event_ids: set[str],
) -> Any:
    if value is None:
        return None
    if field_name.endswith("_event_ids"):
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise EventReferenceError(f"event reference field {field_name} must be a collection")
        return type(value)(
            _resolve_reference(item, allocated, committed_event_ids) for item in value
        )
    if not isinstance(value, str):
        raise EventReferenceError(f"event reference field {field_name} must be a string")
    return _resolve_reference(value, allocated, committed_event_ids)


def _resolve_nested_references(
    value: Any,
    allocated: dict[str, str],
    committed_event_ids: set[str],
) -> Any:
    if isinstance(value, dict):
        resolved: dict[str, Any] = {}
        for key, item in value.items():
            if key.endswith(("_event_id", "_event_ids")):
                resolved[key] = _resolve_reference_field(
                    key,
                    item,
                    allocated,
                    committed_event_ids,
                )
            else:
                resolved[key] = _resolve_nested_references(
                    item,
                    allocated,
                    committed_event_ids,
                )
        return resolved
    if isinstance(value, tuple):
        return tuple(
            _resolve_nested_references(item, allocated, committed_event_ids) for item in value
        )
    if isinstance(value, list):
        return [_resolve_nested_references(item, allocated, committed_event_ids) for item in value]
    return value


def _resolve_event_references(
    event: StoryEvent,
    allocated: dict[str, str],
    committed_event_ids: set[str],
) -> StoryEvent:
    data = _resolve_nested_references(
        event.model_dump(mode="python"),
        allocated,
        committed_event_ids,
    )
    return type(event).model_validate(data)


def prepare_event_batch(
    session_id: str,
    current_revision: int,
    proposals: tuple[ProposedEvent, ...],
    *,
    committed_event_ids: Collection[str] = (),
    event_id_factory: Callable[[], str] = _new_event_id,
) -> tuple[EventEnvelope, ...]:
    if not proposals:
        raise EventReferenceError("event proposal batch cannot be empty")
    local_refs = tuple(proposal.local_ref for proposal in proposals)
    if len(local_refs) != len(set(local_refs)):
        raise EventReferenceError("duplicate local reference")

    committed = set(committed_event_ids)
    allocated = {local_ref: event_id_factory() for local_ref in local_refs}
    allocated_ids = tuple(allocated.values())
    if len(allocated_ids) != len(set(allocated_ids)):
        raise EventReferenceError("event ID factory returned duplicate IDs")
    if set(allocated_ids) & committed:
        raise EventReferenceError("preallocated event ID collides with committed history")

    resolved_events = tuple(
        _resolve_event_references(proposal.event, allocated, committed) for proposal in proposals
    )
    return tuple(
        EventEnvelope(
            event_id=allocated[proposal.local_ref],
            session_id=session_id,
            sequence=current_revision + index,
            event=event,
        )
        for index, (proposal, event) in enumerate(
            zip(proposals, resolved_events, strict=True),
            start=1,
        )
    )
