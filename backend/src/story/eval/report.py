"""Report assembly, markdown rendering, and golden comparison."""

from __future__ import annotations

import json
from typing import Any

from src.story.eval.corpus import CATEGORIES, Corpus, SessionCorpus, parse_iso
from src.story.eval.detectors import Failure, detect_all, flagged_repetition_segments

REPORT_SCHEMA = "story-eval-report/1"

# Regression anchors from the consensus doc: named session/sequence windows
# that must keep exhibiting (or, after the fixes, keep free of) the noted
# failure categories.  ``session_index`` orders by creation time.
ANCHOR_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "S2-seq18-28",
        "session_index": 1,
        "sequence_range": [18, 28],
        "expected_categories": [
            "choice_continuation_miss",
            "scene_time_regression",
            "segment_repetition",
            "detail_contradiction",
        ],
    },
    {
        "name": "S1-seq6-18",
        "session_index": 0,
        "sequence_range": [6, 18],
        "expected_categories": ["detail_contradiction"],
    },
)


def _statistics(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"values": [], "min": None, "max": None, "mean": None}
    return {
        "values": values,
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), 2),
    }


def _session_summary(index: int, session: SessionCorpus) -> dict[str, Any]:
    return {
        "label": f"S{index + 1}",
        "session_id": session.session_id,
        "pack_id": session.pack_id,
        "scene_count": len(session.scenes),
        "segment_count": len(session.segments),
        "block_count": session.block_count,
        "decisions": len(session.decisions),
        "selections": len(session.selections),
    }


def _density(session: SessionCorpus) -> list[int]:
    """Blocks the player reads per decision point (blocks since previous decision)."""
    values: list[int] = []
    consumed = 0
    for decision in session.decisions:
        scenes = [
            scene
            for scene in session.scenes
            if consumed < scene.sequence < decision.sequence
        ]
        values.append(sum(scene.block_count for scene in scenes))
        consumed = decision.sequence
    return values


def _turn_latencies(session: SessionCorpus) -> list[float]:
    """Seconds from each selection to the next committed scene (coarse wall time)."""
    values: list[float] = []
    for selection in session.selections:
        following = [
            scene
            for scene in session.scenes
            if scene.sequence > selection.sequence
        ]
        if not following:
            continue
        delta = parse_iso(following[0].occurred_at) - parse_iso(selection.occurred_at)
        values.append(round(delta.total_seconds(), 1))
    return values


def _anchor_reports(corpus: Corpus, failures: list[Failure]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for spec in ANCHOR_SPECS:
        index = spec["session_index"]
        if index >= len(corpus.sessions):
            continue
        session = corpus.sessions[index]
        low, high = spec["sequence_range"]
        found: set[str] = set()
        matched_failures: list[dict[str, Any]] = []
        for failure in failures:
            if failure.session_id != session.session_id:
                continue
            evidence = failure.evidence_sequences
            if not evidence:
                continue
            in_window = sum(1 for sequence in evidence if low <= sequence <= high)
            if in_window * 2 > len(evidence) or (
                in_window == len(evidence) and in_window > 0
            ):
                found.add(failure.category)
                matched_failures.append(
                    {
                        "category": failure.category,
                        "subkind": failure.subkind,
                        "evidence_sequences": list(evidence),
                    }
                )
        expected = set(spec["expected_categories"])
        reports.append(
            {
                "name": spec["name"],
                "session_id": session.session_id,
                "sequence_range": spec["sequence_range"],
                "expected_categories": sorted(expected),
                "found_categories": sorted(found),
                "pass": expected <= found,
                "failures": matched_failures,
            }
        )
    return reports


def build_report(corpus: Corpus) -> dict[str, Any]:
    """Assemble the deterministic evaluation report for a corpus."""
    failures = detect_all(corpus)
    category_counts = {category: 0 for category in CATEGORIES}
    for failure in failures:
        category_counts[failure.category] += 1

    selections_total = sum(len(session.selections) for session in corpus.sessions)
    continuation_misses = category_counts["choice_continuation_miss"]
    # Selections whose consequence segment exists are the denominator.
    with_outcome = 0
    for session in corpus.sessions:
        for selection in session.selections:
            if any(
                min(segment.sequences) > selection.sequence for segment in session.segments
            ):
                with_outcome += 1
    continuation_rate = (
        round((with_outcome - continuation_misses) / with_outcome, 4) if with_outcome else None
    )

    repetition_flagged_segments = sum(
        len(flagged_repetition_segments(session)) for session in corpus.sessions
    )
    total_segments = sum(len(session.segments) for session in corpus.sessions)

    density_values = [value for session in corpus.sessions for value in _density(session)]
    latency_values = [value for session in corpus.sessions for value in _turn_latencies(session)]

    return {
        "schema": REPORT_SCHEMA,
        "sessions": [
            _session_summary(index, session) for index, session in enumerate(corpus.sessions)
        ],
        "failures": [failure.as_dict() for failure in failures],
        "metrics": {
            "category_counts": category_counts,
            "total_failures": len(failures),
            "block_count": corpus.block_count,
            "failures_per_block": round(len(failures) / corpus.block_count, 4)
            if corpus.block_count
            else None,
            "choice_continuation_rate": continuation_rate,
            "selections_total": selections_total,
            "selections_with_outcome": with_outcome,
            "repetition_flagged_segments": repetition_flagged_segments,
            "segment_count": total_segments,
            "repetition_segment_rate": round(repetition_flagged_segments / total_segments, 4)
            if total_segments
            else None,
            "blocks_per_decision": _statistics([float(v) for v in density_values]),
            "turn_latency_seconds": _statistics([float(v) for v in latency_values]),
        },
        "anchors": _anchor_reports(corpus, failures),
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_markdown(report: dict[str, Any], title: str) -> str:
    """Human-readable baseline report."""
    lines: list[str] = [f"# {title}", ""]
    metrics = report["metrics"]
    lines += ["## 指标", ""]
    lines += [
        (
            f"- 会话数:{len(report['sessions'])},总块数:{metrics['block_count']},"
            f"总失败:{metrics['total_failures']}"
            f"(每块 {metrics['failures_per_block']})"
        ),
        f"- 五类失败:{json.dumps(metrics['category_counts'], ensure_ascii=False)}",
        (
            f"- 选择承接率:{metrics['choice_continuation_rate']}"
            f"({metrics['selections_total'] - metrics['category_counts']['choice_continuation_miss']}"
            f"/{metrics['selections_with_outcome']})"
        ),
        f"- 复读率:{metrics['repetition_flagged_segments']}/{metrics['segment_count']} 段",
        f"- 密度(块/决策):{metrics['blocks_per_decision']}",
        f"- 回合延迟(秒,选择→下场提交):{metrics['turn_latency_seconds']}",
        "",
    ]
    lines += ["## 锚点", ""]
    for anchor in report["anchors"]:
        status = "✓" if anchor["pass"] else "✗"
        lines += [
            (
                f"- {status} **{anchor['name']}** 期望 {anchor['expected_categories']},"
                f"实得 {anchor['found_categories']}"
            )
        ]
    lines += ["", "## 失败明细", ""]
    for failure in report["failures"]:
        subkind = f" [{failure.get('subkind')}]" if failure.get("subkind") else ""
        lines += [
            f"### {failure['category_label']}{subkind} — {failure['session_id'][:8]}",
            f"- 证据序列:{failure['evidence_sequences']}",
            f"- {failure['detail']}",
        ]
        for text in failure["evidence_text"]:
            lines.append(f"  - {text}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Golden comparison
# ---------------------------------------------------------------------------


def _diff(path: str, expected: Any, actual: Any) -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                differences.extend(_diff(f"{path}.{key}", "<missing>", actual[key]))
            elif key not in actual:
                differences.extend(_diff(f"{path}.{key}", expected[key], "<missing>"))
            else:
                differences.extend(_diff(f"{path}.{key}", expected[key], actual[key]))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}: list length {len(expected)} -> {len(actual)}"]
        differences = []
        for index, (left, right) in enumerate(zip(expected, actual)):
            differences.extend(_diff(f"{path}[{index}]", left, right))
        return differences
    if expected != actual:
        return [f"{path}: {expected!r} -> {actual!r}"]
    return []


def compare_reports(golden: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Structural diff between a golden report and a fresh one."""
    return _diff("$", golden, current)
