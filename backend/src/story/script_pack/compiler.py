from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from src.story.conditions import (
    ConditionEvaluationError,
    ConditionProgram,
    ConditionSyntaxError,
    compile_condition,
)
from src.story.script_pack.models import (
    CompiledScriptPack,
    CompletionEvidenceSource,
    ScriptPackSource,
    ScriptPackSourceV1,
    ScriptPackSourceV2,
)

STANDARD_ACTION_IDS = frozenset(
    {
        "ask",
        "observe",
        "support",
        "challenge",
        "withhold",
        "disclose",
        "follow",
        "leave",
    }
)

_INCLUDE_KEYS = frozenset(
    {
        "protagonist",
        "world",
        "characters",
        "facts",
        "goals",
        "interaction_rules",
        "endings",
        "assets",
        "world_setting",
        "story_history",
        "opening_state",
        "completion_requirements",
        "conflict_axes",
        "relationship_event_tags",
        "relationship_turning_points",
        "obligation_kinds",
    }
)

_CONDITION_ROOTS = frozenset(
    {
        "facts",
        "relationships",
        "goals",
        "world",
        "session",
        "threads",
    }
)


class PackCompileError(ValueError):
    def __init__(self, errors: str | Iterable[str]) -> None:
        if isinstance(errors, str):
            self.errors = (errors,)
        else:
            self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


def _yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackCompileError(f"script pack file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PackCompileError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackCompileError(f"expected a YAML mapping in {path}")
    return data


def _included_value(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackCompileError(f"included file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PackCompileError(f"invalid YAML in {path}: {exc}") from exc


def load_script_pack_source(pack_path: Path | str) -> ScriptPackSource:
    supplied = Path(pack_path)
    manifest = supplied if supplied.is_file() else supplied / "pack.yaml"
    root = manifest.parent.resolve()
    raw = _yaml_mapping(manifest)
    includes = raw.pop("includes", {})

    if not isinstance(includes, dict):
        raise PackCompileError("includes must be a mapping of field to relative file")

    for key, relative in includes.items():
        if key not in _INCLUDE_KEYS:
            raise PackCompileError(f"unsupported include field: {key}")
        if key in raw:
            raise PackCompileError(f"field {key} cannot be inline and included")
        if not isinstance(relative, str) or not relative:
            raise PackCompileError(f"include path for {key} must be a string")

        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root):
            raise PackCompileError(f"include for {key} must stay inside the pack directory")
        raw[key] = _included_value(resolved)

    try:
        return ScriptPackSource.model_validate(raw)
    except ValidationError as exc:
        raise PackCompileError(str(exc)) from exc


def _duplicate_ids(label: str, values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return [
        f"duplicate {label} id: {value}" for value, count in sorted(counts.items()) if count > 1
    ]


def _v1_condition_entries(source: ScriptPackSourceV1) -> Iterable[tuple[str, str]]:
    for fact in source.facts.derived:
        yield f"fact.{fact.id}.derived", fact.condition
    for question in source.facts.latent_questions:
        for candidate in question.candidates:
            for index, expression in enumerate(candidate.requirements):
                yield (
                    f"fact.{question.id}.candidate.{candidate.value}.requirement.{index}",
                    expression,
                )
    for goal in source.goals:
        yield f"goal.{goal.id}.success", goal.success_condition
        yield f"goal.{goal.id}.failure", goal.failure_condition
    for action in source.interaction_rules.extensions:
        for index, expression in enumerate(action.preconditions):
            yield f"action.{action.id}.precondition.{index}", expression
    for ending in source.endings:
        for group_name in ("all", "any", "none"):
            for index, expression in enumerate(getattr(ending.eligibility, group_name)):
                yield f"ending.{ending.id}.{group_name}.{index}", expression


def _condition_reference_errors(
    programs: Mapping[str, ConditionProgram],
    character_ids: set[str],
    fact_ids: set[str],
    goal_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    for condition_key, program in programs.items():
        for dotted in program.paths:
            parts = dotted.split(".")
            root = parts[0]
            if root not in _CONDITION_ROOTS:
                errors.append(f"{condition_key}: unsupported condition root {root}")
                continue
            if root == "facts" and (len(parts) < 2 or parts[1] not in fact_ids):
                errors.append(f"{condition_key}: unknown fact in path {dotted}")
            if root == "goals" and (len(parts) < 2 or parts[1] not in goal_ids):
                errors.append(f"{condition_key}: unknown goal in path {dotted}")
            if root == "relationships" and (len(parts) < 2 or parts[1] not in character_ids):
                errors.append(f"{condition_key}: unknown character in path {dotted}")
    return errors


def _compile_programs_from(
    entries: Iterable[tuple[str, str]],
) -> tuple[dict[str, ConditionProgram], list[str]]:
    programs: dict[str, ConditionProgram] = {}
    errors: list[str] = []
    for key, expression in entries:
        try:
            programs[key] = compile_condition(expression)
        except ConditionSyntaxError as exc:
            errors.append(f"{key}: {exc}")
    return programs, errors


def _shared_reference_errors(
    source: ScriptPackSourceV1 | ScriptPackSourceV2,
    character_ids: set[str],
    fixed_ids: set[str],
    fact_ids: set[str],
    goal_ids: set[str],
    action_ids: set[str],
) -> list[str]:
    """Version-independent structural reference validations.

    These checks (known_by, knowledge, secrets, capabilities, goal owner,
    goal conflicts, latent candidate duplicates) are equally meaningful for
    v1.0 and v2.0 packs and must run in both code paths.
    """
    errors: list[str] = []
    fixed_known_by = {item.id: set(item.known_by) for item in source.facts.fixed}

    for fact in source.facts.fixed:
        for character_id in fact.known_by:
            if character_id not in character_ids:
                errors.append(f"fact {fact.id} known_by references {character_id}")

    for character in source.characters:
        for fact_id in character.knowledge:
            if fact_id not in fact_ids:
                errors.append(f"character {character.id} references unknown fact {fact_id}")
            elif fact_id not in fixed_ids:
                errors.append(f"opening knowledge must be fixed: {character.id} -> {fact_id}")
            elif character.id not in fixed_known_by[fact_id]:
                errors.append(
                    "opening knowledge is not granted by fact known_by: "
                    f"{character.id} -> {fact_id}"
                )
        for fact_id in character.secrets:
            if fact_id not in fact_ids:
                errors.append(f"character {character.id} references unknown fact {fact_id}")
        for action_id in character.capabilities:
            if action_id not in action_ids:
                errors.append(f"character {character.id} has unknown action {action_id}")

    for action_id in source.protagonist.capabilities:
        if action_id not in action_ids:
            errors.append(f"protagonist has unknown action {action_id}")

    owners = character_ids | {source.protagonist.id}
    for goal in source.goals:
        if goal.owner not in owners:
            errors.append(f"goal {goal.id} has unknown owner {goal.owner}")
        for conflict in goal.conflicts_with:
            if conflict not in goal_ids:
                errors.append(f"goal {goal.id} conflicts with unknown goal {conflict}")

    for question in source.facts.latent_questions:
        errors.extend(
            _duplicate_ids(
                f"candidate value for {question.id}",
                (candidate.value for candidate in question.candidates),
            )
        )
    return errors


def _reference_errors(
    source: ScriptPackSourceV1,
    character_ids: set[str],
    fixed_ids: set[str],
    fact_ids: set[str],
    goal_ids: set[str],
    action_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    location_ids = {item.id for item in source.world.locations}

    if source.world.initial_situation.location not in location_ids:
        errors.append(
            "initial_situation references unknown location "
            f"{source.world.initial_situation.location}"
        )
    for character_id in source.world.initial_situation.present_characters:
        if character_id not in character_ids:
            errors.append(f"initial_situation references unknown character {character_id}")
    for fact_id in source.world.initial_situation.known_facts:
        if fact_id not in fact_ids:
            errors.append(f"initial_situation references unknown fact {fact_id}")
        elif fact_id not in fixed_ids:
            errors.append(f"opening known fact must be fixed: {fact_id}")

    errors.extend(
        _shared_reference_errors(source, character_ids, fixed_ids, fact_ids, goal_ids, action_ids)
    )
    return errors


def _has_guaranteed_fallback(
    source: ScriptPackSourceV1,
    programs: Mapping[str, ConditionProgram],
) -> bool:
    context = {"session": {"scene_count": source.experience.max_scenes}}
    for ending in source.endings:
        if ending.type != "fallback":
            continue
        if ending.eligibility.any or ending.eligibility.none:
            continue
        keys = [f"ending.{ending.id}.all.{index}" for index in range(len(ending.eligibility.all))]
        try:
            if all(
                set(programs[key].paths) <= {"session.scene_count"}
                and programs[key].evaluate(context)
                for key in keys
            ):
                return True
        except ConditionEvaluationError:
            continue
    return False


# ---------------------------------------------------------------------------
# v2.0 helpers
# ---------------------------------------------------------------------------


def _v2_reference_errors(
    source: ScriptPackSourceV2,
    character_ids: set[str],
    fixed_ids: set[str],
    fact_ids: set[str],
    goal_ids: set[str],
) -> list[str]:
    """Validate references specific to v2.0 packs."""
    errors: list[str] = []
    location_ids = {item.id for item in source.world_setting.locations}

    # opening_state.location must be a known location
    if source.opening_state.location not in location_ids:
        errors.append(f"opening_state references unknown location {source.opening_state.location}")

    # opening_state.present_characters must be known characters
    for character_id in source.opening_state.present_characters:
        if character_id not in character_ids:
            errors.append(f"opening_state references unknown character {character_id}")

    # opening_state.known_facts must be fixed facts
    for fact_id in source.opening_state.known_facts:
        if fact_id not in fact_ids:
            errors.append(f"opening_state references unknown fact {fact_id}")
        elif fact_id not in fixed_ids:
            errors.append(f"opening known fact must be fixed: {fact_id}")

    del goal_ids
    tag_ids = {item.id for item in source.relationship_event_tags}
    turning_point_ids = {item.id for item in source.relationship_turning_points}
    revealable_fact_ids = {
        item.id
        for item in source.facts.fixed
        if item.visibility == "hidden" and item.id not in source.opening_state.known_facts
    } | {item.id for item in source.facts.latent_questions}

    for axis in source.conflict_axes:
        for character_id in axis.source_character_ids:
            if character_id not in character_ids:
                errors.append(
                    f"conflict axis {axis.id} references unknown character {character_id}"
                )

    for turning_point in source.relationship_turning_points:
        if turning_point.character_id not in character_ids:
            errors.append(
                f"turning point {turning_point.id} references unknown character "
                f"{turning_point.character_id}"
            )
        for tag in turning_point.all_of_event_tags:
            if tag not in tag_ids:
                errors.append(f"turning point {turning_point.id} references unknown tag {tag}")

    for requirement in source.completion_requirements:
        for leaf in _walk_completion_evidence(requirement):
            if leaf.fact_revealed is not None and leaf.fact_revealed.fact_id not in fact_ids:
                errors.append(
                    f"completion requirement {requirement.id} references unknown fact "
                    f"{leaf.fact_revealed.fact_id}"
                )
            elif (
                leaf.fact_revealed is not None
                and leaf.fact_revealed.fact_id not in revealable_fact_ids
            ):
                errors.append(
                    f"completion requirement {requirement.id} fact "
                    f"{leaf.fact_revealed.fact_id} cannot produce a FactRevealed event"
                )
            if (
                leaf.relationship_turning_point is not None
                and leaf.relationship_turning_point.turning_point_id not in turning_point_ids
            ):
                errors.append(
                    f"completion requirement {requirement.id} references unknown turning point "
                    f"{leaf.relationship_turning_point.turning_point_id}"
                )
            if leaf.obligation_fulfilled is not None:
                minimum_burden = leaf.obligation_fulfilled.min_burden
                if not any(
                    obligation.burden >= minimum_burden
                    and "fulfilled" in obligation.allowed_outcomes
                    for obligation in source.obligation_kinds
                ):
                    errors.append(
                        f"completion requirement {requirement.id} has no fulfillable "
                        f"obligation kind with burden >= {minimum_burden}"
                    )

    return errors


def _walk_completion_evidence(
    node: CompletionEvidenceSource,
) -> Iterable[CompletionEvidenceSource]:
    if node.all is not None:
        for child in node.all:
            yield from _walk_completion_evidence(child)
    elif node.any is not None:
        for child in node.any:
            yield from _walk_completion_evidence(child)
    else:
        yield node


def _v2_condition_entries(source: ScriptPackSourceV2) -> Iterable[tuple[str, str]]:
    """Yield condition key/expression pairs for v2.0 packs (no ending conditions)."""
    for fact in source.facts.derived:
        yield f"fact.{fact.id}.derived", fact.condition
    for question in source.facts.latent_questions:
        for candidate in question.candidates:
            for index, expression in enumerate(candidate.requirements):
                yield (
                    f"fact.{question.id}.candidate.{candidate.value}.requirement.{index}",
                    expression,
                )
    for goal in source.goals:
        yield f"goal.{goal.id}.success", goal.success_condition
        yield f"goal.{goal.id}.failure", goal.failure_condition
    for action in source.interaction_rules.extensions:
        for index, expression in enumerate(action.preconditions):
            yield f"action.{action.id}.precondition.{index}", expression


def _pack_locations(source: ScriptPackSourceV1 | ScriptPackSourceV2):
    if isinstance(source, ScriptPackSourceV2):
        return source.world_setting.locations
    return source.world.locations


def _pack_factions(source: ScriptPackSourceV1 | ScriptPackSourceV2):
    if isinstance(source, ScriptPackSourceV2):
        return source.world_setting.factions
    return source.world.factions


def compile_source(
    raw: Mapping[str, Any] | ScriptPackSource,
) -> CompiledScriptPack:
    if isinstance(raw, ScriptPackSource):
        source = raw
    else:
        version = raw.get("schema_version", "1.0")
        if version not in ("1.0", "2.0"):
            raise PackCompileError(f"Unknown schema_version: {version!r} (supported: '1.0', '2.0')")
        try:
            source = (
                ScriptPackSourceV1.model_validate(raw)
                if version == "1.0"
                else ScriptPackSourceV2.model_validate(raw)
            )
        except ValidationError as exc:
            raise PackCompileError(str(exc)) from exc

    character_ids = {item.id for item in source.characters}
    fixed_ids = {item.id for item in source.facts.fixed}
    latent_ids = {item.id for item in source.facts.latent_questions}
    derived_ids = {item.id for item in source.facts.derived}
    fact_ids = fixed_ids | latent_ids | derived_ids
    goal_ids = {item.id for item in source.goals}
    extension_id_values = [item.id for item in source.interaction_rules.extensions]
    extension_ids = set(extension_id_values)
    action_ids = (set(source.interaction_rules.enabled_standard) | extension_ids) - set(
        source.interaction_rules.disabled
    )

    errors: list[str] = []
    errors.extend(_duplicate_ids("character", (item.id for item in source.characters)))
    fact_id_values = [
        *(item.id for item in source.facts.fixed),
        *(item.id for item in source.facts.latent_questions),
        *(item.id for item in source.facts.derived),
    ]
    errors.extend(_duplicate_ids("fact", fact_id_values))
    errors.extend(_duplicate_ids("goal", (item.id for item in source.goals)))
    errors.extend(_duplicate_ids("location", (item.id for item in _pack_locations(source))))
    errors.extend(_duplicate_ids("faction", (item.id for item in _pack_factions(source))))
    errors.extend(_duplicate_ids("action", extension_id_values))
    if source.protagonist.id in character_ids:
        errors.append(f"protagonist id collides with character id: {source.protagonist.id}")
    errors.extend(
        f"action extension cannot replace standard action: {item}"
        for item in sorted(extension_ids & STANDARD_ACTION_IDS)
    )

    unknown_standard = set(source.interaction_rules.enabled_standard) - STANDARD_ACTION_IDS
    errors.extend(f"unknown standard action: {item}" for item in sorted(unknown_standard))
    unknown_disabled = set(source.interaction_rules.disabled) - (
        STANDARD_ACTION_IDS | extension_ids
    )
    errors.extend(f"unknown disabled action: {item}" for item in sorted(unknown_disabled))

    # --- Schema-version-specific validation ---
    is_v2 = isinstance(source, ScriptPackSourceV2)

    if is_v2:
        errors.extend(
            _duplicate_ids(
                "completion_requirement",
                (req.id for req in source.completion_requirements),
            )
        )
        errors.extend(_duplicate_ids("conflict_axis", (item.id for item in source.conflict_axes)))
        errors.extend(
            _duplicate_ids(
                "relationship_event_tag",
                (item.id for item in source.relationship_event_tags),
            )
        )
        errors.extend(
            _duplicate_ids(
                "relationship_turning_point",
                (item.id for item in source.relationship_turning_points),
            )
        )
        errors.extend(
            _duplicate_ids("obligation_kind", (item.id for item in source.obligation_kinds))
        )
        for axis in source.conflict_axes:
            errors.extend(_duplicate_ids(f"value in conflict_axis {axis.id}", axis.values))
            errors.extend(
                _duplicate_ids(
                    f"source_character in conflict_axis {axis.id}",
                    axis.source_character_ids,
                )
            )
        for turning_point in source.relationship_turning_points:
            errors.extend(
                _duplicate_ids(
                    f"event_tag in relationship_turning_point {turning_point.id}",
                    turning_point.all_of_event_tags,
                )
            )
        for obligation_kind in source.obligation_kinds:
            errors.extend(
                _duplicate_ids(
                    f"outcome in obligation_kind {obligation_kind.id}",
                    obligation_kind.allowed_outcomes,
                )
            )
        errors.extend(_v2_reference_errors(source, character_ids, fixed_ids, fact_ids, goal_ids))
        errors.extend(
            _shared_reference_errors(
                source, character_ids, fixed_ids, fact_ids, goal_ids, action_ids
            )
        )
        programs, condition_errors = _compile_programs_from(_v2_condition_entries(source))
        ending_ids: set[str] = set()
        completion_requirement_ids = {req.id for req in source.completion_requirements}
    else:
        # v1.0 path
        errors.extend(_duplicate_ids("ending", (item.id for item in source.endings)))
        normal_endings = [item for item in source.endings if item.type != "fallback"]
        fallback_endings = [item for item in source.endings if item.type == "fallback"]
        if len(normal_endings) < 3:
            errors.append("script pack requires at least 3 normal endings")
        if not fallback_endings:
            errors.append("script pack requires at least 1 fallback ending")

        errors.extend(
            _reference_errors(
                source,
                character_ids,
                fixed_ids,
                fact_ids,
                goal_ids,
                action_ids,
            )
        )
        programs, condition_errors = _compile_programs_from(_v1_condition_entries(source))
        ending_ids = {item.id for item in source.endings}
        completion_requirement_ids: set[str] = set()

    errors.extend(condition_errors)
    errors.extend(_condition_reference_errors(programs, character_ids, fact_ids, goal_ids))

    if not is_v2 and not condition_errors and not _has_guaranteed_fallback(source, programs):
        errors.append(
            "script pack requires a guaranteed fallback that is true at "
            "max_scenes and depends only on session.scene_count"
        )

    if errors:
        raise PackCompileError(errors)

    canonical = json.dumps(
        source.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return CompiledScriptPack(
        source=source,
        pack_hash=hashlib.sha256(canonical).hexdigest(),
        conditions=programs,
        character_ids=frozenset(character_ids),
        fact_ids=frozenset(fact_ids),
        goal_ids=frozenset(goal_ids),
        ending_ids=frozenset(ending_ids),
        completion_requirement_ids=frozenset(completion_requirement_ids),
        action_ids=frozenset(action_ids),
    )


def compile_script_pack(pack_path: Path | str) -> CompiledScriptPack:
    return compile_source(load_script_pack_source(pack_path))
