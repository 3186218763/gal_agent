"""Offline eval harness tests (spec seam 3).

Synthetic mini-corpora drive every detector, the metrics math, and the
golden-comparison CLI.  The real seed corpus lives in ``data/story-v2.db``
(outside version control) and is only exercised by the archived baseline.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.story.eval.__main__ import main as eval_main
from src.story.eval.corpus import load_corpus
from src.story.eval.detectors import (
    detect_choice_continuation_misses,
    detect_detail_contradictions,
    detect_quote_style_breaks,
    detect_scene_time_regressions,
    detect_segment_repetitions,
)
from src.story.eval.report import build_report, compare_reports

# ---------------------------------------------------------------------------
# Synthetic corpus builder
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE story_sessions (
    session_id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL,
    pack_hash TEXT NOT NULL,
    revision INTEGER NOT NULL,
    snapshot_revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE story_events (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    event_json TEXT NOT NULL,
    PRIMARY KEY (session_id, sequence)
);
"""


def block(kind: str, text: str, character_id: str | None = None) -> dict:
    return {"kind": kind, "text": text, "character_id": character_id}


def write_corpus(
    path: Path,
    sessions: list[dict],
) -> Path:
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA)
    for session in sessions:
        db.execute(
            "INSERT INTO story_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session["session_id"],
                "test_pack",
                "hash",
                len(session["events"]),
                0,
                "{}",
                session.get("created_at", "2026-08-15T00:00:00+00:00"),
            ),
        )
        for sequence, event in enumerate(session["events"], start=1):
            kind = event["type"]
            payload = dict(event)
            payload["type"] = kind
            envelope = {
                "event_id": f"{session['session_id']}:{sequence}",
                "session_id": session["session_id"],
                "sequence": sequence,
                "occurred_at": event.get(
                    "occurred_at", f"2026-08-15T00:{sequence // 60:02d}:{sequence % 60:02d}Z"
                ),
                "event": payload,
            }
            db.execute(
                "INSERT INTO story_events VALUES (?, ?, ?, ?)",
                (
                    session["session_id"],
                    sequence,
                    envelope["event_id"],
                    json.dumps(envelope, ensure_ascii=False),
                ),
            )
    db.commit()
    db.close()
    return path


def scene_event(scene_id: str, blocks: list[dict], present=("alice", "bob")) -> dict:
    return {
        "type": "scene_committed",
        "scene_id": scene_id,
        "terminal": "continue",
        "present_character_ids": list(present),
        "blocks": blocks,
    }


def decision_event(decision_id: str, choices: list[dict]) -> dict:
    return {"type": "decision_presented", "decision_id": decision_id, "choices": choices}


def selection_event(decision_id: str, option_id: str, **extra) -> dict:
    return {
        "type": "player_action_selected",
        "decision_id": decision_id,
        "option_id": option_id,
        "action_id": "ask",
        "intent": extra.get("intent", ""),
        "target_character_id": extra.get("target_character_id"),
        "idempotency_key": "key",
        "occurred_at": extra.get(
            "occurred_at", f"2026-08-15T00:00:{extra.get('sequence', 3):02d}Z"
        ),
    }


def choice(option_id: str, label: str, target: str) -> dict:
    return {
        "id": option_id,
        "action_id": "ask",
        "label": label,
        "intent": label,
        "target_character_id": target,
    }


@pytest.fixture()
def corpus_factory(tmp_path):
    def factory(sessions: list[dict], name: str = "eval.db") -> object:
        return load_corpus(write_corpus(tmp_path / name, sessions))

    return factory


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


def test_segments_split_at_decisions_and_labels_resolve(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("narration", "一")]),
                scene_event("s2", [block("narration", "二")]),
                decision_event(
                    "dec-1", [choice("opt-a", "第一问", "alice"), choice("opt-b", "第二问", "bob")]
                ),
                selection_event("dec-1", "opt-a", target_character_id="alice"),
                scene_event("s3", [block("narration", "三")]),
                decision_event(
                    "dec-2", [choice("opt-c", "第三问", "alice"), choice("opt-d", "第四问", "bob")]
                ),
            ],
        }
    ]
    corpus = corpus_factory(sessions)
    session = corpus.sessions[0]
    assert [segment.block_count for segment in session.segments] == [2, 1]
    assert session.selections[0].label == "第一问"  # from dec-1, not dec-2's opt-c


# ---------------------------------------------------------------------------
# ① choice continuation
# ---------------------------------------------------------------------------


def test_choice_miss_target_absent(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", "鲍勃开场", "bob")], present=("bob",)),
                decision_event("dec-1", [choice("opt-a", "询问艾丽丝", "alice")]),
                selection_event("dec-1", "opt-a", target_character_id="alice"),
                scene_event("s2", [block("narration", "只有鲍勃的场景")], present=("bob",)),
            ],
        }
    ]
    failures = detect_choice_continuation_misses(corpus_factory(sessions).sessions[0])
    assert [failure.subkind for failure in failures] == ["target_absent"]


def test_choice_miss_engagement_reset(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", "艾丽丝求助。", "alice")]),
                decision_event("dec-1", [choice("opt-a", "观察美奈", "alice")]),
                selection_event("dec-1", "opt-a", target_character_id="alice"),
                scene_event(
                    "s2",
                    [block("dialogue", "这位朋友,你最好不要打听不该卷入的事情。", "bob")],
                ),
            ],
        }
    ]
    failures = detect_choice_continuation_misses(corpus_factory(sessions).sessions[0])
    assert [failure.subkind for failure in failures] == ["engagement_reset"]


def test_choice_miss_pledge_unexecuted(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", "艾丽丝求助。", "alice")]),
                decision_event("dec-1", [choice("opt-a", "我和你一起找找看吧", "alice")]),
                selection_event("dec-1", "opt-a", target_character_id="alice"),
                # Dialogue mentions finding, narration never executes it, and
                # "寻常" must not fake a narration search hint.
                scene_event(
                    "s2",
                    [
                        block("narration", "气氛不同寻常地凝重。"),
                        block("dialogue", "希望能赶快找回来。", "alice"),
                    ],
                ),
            ],
        }
    ]
    failures = detect_choice_continuation_misses(corpus_factory(sessions).sessions[0])
    assert [failure.subkind for failure in failures] == ["pledge_unexecuted"]


def test_choice_honored_by_executed_pledge(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", "艾丽丝求助。", "alice")]),
                decision_event("dec-1", [choice("opt-a", "我和你一起找找看吧", "alice")]),
                selection_event("dec-1", "opt-a", target_character_id="alice"),
                scene_event(
                    "s2",
                    [block("narration", "我弯下腰,和她一起寻找桌底的阴影。")],
                ),
            ],
        }
    ]
    failures = detect_choice_continuation_misses(corpus_factory(sessions).sessions[0])
    assert failures == []


def test_choice_miss_formulaic_target(corpus_factory):
    recycled = "欢迎光临。请随意挑选空位,今天的单品豆风味很柔和。"
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", recycled, "alice")]),
                decision_event("dec-1", [choice("opt-a", "向艾丽丝打听", "alice")]),
                selection_event("dec-1", "opt-a", target_character_id="alice"),
                scene_event(
                    "s2",
                    [
                        block(
                            "dialogue",
                            recycled + "如果有什么遗落的物品,不妨平心静气地回想一下。",
                            "alice",
                        ),
                    ],
                ),
            ],
        }
    ]
    failures = detect_choice_continuation_misses(corpus_factory(sessions).sessions[0])
    assert [failure.subkind for failure in failures] == ["formulaic_target"]


# ---------------------------------------------------------------------------
# ② scene time regression
# ---------------------------------------------------------------------------


def test_time_regression_and_deadline_exclusion(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                # Deadline mention ("傍晚六点打烊") is not the scene's clock.
                scene_event("s1", [block("narration", "清晨的阳光洒进来,我们傍晚六点打烊。")]),
                scene_event("s2", [block("narration", "午后的阳光照在桌上。")]),
                scene_event("s3", [block("narration", "窗外天色渐暗,店里安静下来。")]),
                scene_event("s4", [block("narration", "午后的阳光又一次铺满桌面。")]),
            ],
        }
    ]
    failures = detect_scene_time_regressions(corpus_factory(sessions).sessions[0])
    # s1(清晨=1) -> s2(午后=3) forward is fine; s3(渐暗=5) -> s4(午后=3) regresses.
    assert len(failures) == 1
    assert failures[0].evidence_sequences == (3, 4)


# ---------------------------------------------------------------------------
# ③ quote style break
# ---------------------------------------------------------------------------


def test_quote_style_break_once_per_session(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", "你好,没有引号。", "alice")]),
                decision_event("dec-1", [choice("opt-a", "问", "alice")]),
                scene_event("s2", [block("dialogue", "「这次是直角引号。」", "alice")]),
                decision_event("dec-2", [choice("opt-b", "再问", "alice")]),
                scene_event("s3", [block("dialogue", "又回到没有引号。", "alice")]),
                decision_event("dec-3", [choice("opt-c", "还问", "alice")]),
            ],
        }
    ]
    failures = detect_quote_style_breaks(corpus_factory(sessions).sessions[0])
    assert len(failures) == 1
    assert "2 处变化" in failures[0].detail


def test_quote_style_stable_session_no_failure(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", "「开场。」", "alice")]),
                decision_event("dec-1", [choice("opt-a", "问", "alice")]),
                scene_event("s2", [block("dialogue", "「继续。」", "alice")]),
                decision_event("dec-2", [choice("opt-b", "再问", "alice")]),
            ],
        }
    ]
    assert detect_quote_style_breaks(corpus_factory(sessions).sessions[0]) == []


# ---------------------------------------------------------------------------
# ④ segment repetition
# ---------------------------------------------------------------------------


def test_repetition_flags_recycled_gestures(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event(
                    "s1",
                    [
                        block("dialogue", "我们傍晚六点就会准时打烊。", "alice"),
                        block("narration", "鲍勃推了推鼻梁上的眼镜。"),
                    ],
                ),
                decision_event("dec-1", [choice("opt-a", "问", "alice")]),
                scene_event(
                    "s2",
                    [
                        block("narration", "他推了推鼻梁上的眼镜,保持沉默。"),
                        block("dialogue", "我们傍晚六点就会准时打烊的。", "alice"),
                    ],
                ),
                decision_event("dec-2", [choice("opt-b", "再问", "alice")]),
            ],
        }
    ]
    failures = detect_segment_repetitions(corpus_factory(sessions).sessions[0])
    assert len(failures) == 1
    assert "推了推鼻梁上的眼镜" in failures[0].detail


def test_repetition_ignores_connective_noise(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("narration", "在这个清晨,他开始了旅途。")]),
                decision_event("dec-1", [choice("opt-a", "问", "alice")]),
                scene_event(
                    "s2", [block("narration", "在这个夜晚,她结束了漂泊,望着远方的灯塔。")]
                ),
                decision_event("dec-2", [choice("opt-b", "再问", "alice")]),
            ],
        }
    ]
    assert detect_segment_repetitions(corpus_factory(sessions).sessions[0]) == []


# ---------------------------------------------------------------------------
# ⑤ detail contradictions
# ---------------------------------------------------------------------------


def test_notebook_color_contradiction(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", "那本黑皮笔记本很重要。", "alice")]),
                decision_event("dec-1", [choice("opt-a", "问", "alice")]),
                scene_event(
                    "s2", [block("dialogue", "封面是深蓝色的硬皮本,记录着符号。", "alice")]
                ),
                decision_event("dec-2", [choice("opt-b", "再问", "alice")]),
            ],
        }
    ]
    failures = detect_detail_contradictions(corpus_factory(sessions).sessions[0])
    assert [failure.subkind for failure in failures] == ["notebook_color"]
    assert "黑 → 深蓝" in failures[0].detail


def test_last_seen_location_contradiction(corpus_factory):
    sessions = [
        {
            "session_id": "sess-1",
            "events": [
                scene_event("s1", [block("dialogue", "我把它放在手边的。", "alice")]),
                decision_event("dec-1", [choice("opt-a", "问", "alice")]),
                scene_event(
                    "s2",
                    [block("dialogue", "笔记本还在背包的侧袋里才对。", "alice")],
                ),
                decision_event("dec-2", [choice("opt-b", "再问", "alice")]),
                scene_event("s3", [block("dialogue", "它明明还放在提包里的。", "alice")]),
            ],
        }
    ]
    failures = detect_detail_contradictions(corpus_factory(sessions).sessions[0])
    assert [failure.subkind for failure in failures] == ["last_seen_location"]


# ---------------------------------------------------------------------------
# Report, determinism, golden comparison
# ---------------------------------------------------------------------------


def _report_session() -> dict:
    return {
        "session_id": "sess-1",
        "events": [
            scene_event("s1", [block("dialogue", "那本黑皮笔记本很重要。", "alice")]),
            decision_event("dec-1", [choice("opt-a", "问艾丽丝", "alice")]),
            selection_event(
                "dec-1", "opt-a", target_character_id="alice", occurred_at="2026-08-15T00:00:10Z"
            ),
            {
                "type": "scene_committed",
                "scene_id": "s2",
                "terminal": "continue",
                "present_character_ids": ["alice"],
                "occurred_at": "2026-08-15T00:01:10Z",
                "blocks": [block("dialogue", "封面是深蓝色的硬皮本。", "alice")],
            },
            decision_event("dec-2", [choice("opt-b", "再问", "alice")]),
        ],
    }


def test_report_metrics_and_determinism(tmp_path, corpus_factory):
    corpus = corpus_factory([_report_session()])
    first = build_report(corpus)
    second = build_report(
        load_corpus(write_corpus(tmp_path / "eval-again.db", [_report_session()]))
    )
    assert first == second
    metrics = first["metrics"]
    assert metrics["block_count"] == 2
    assert metrics["category_counts"]["detail_contradiction"] == 1
    assert metrics["choice_continuation_rate"] == 1.0
    assert metrics["blocks_per_decision"]["values"] == [1.0, 1.0]
    assert metrics["turn_latency_seconds"]["values"] == [60.0]


def test_golden_comparison_detects_drift(corpus_factory):
    corpus = corpus_factory([_report_session()])
    golden = build_report(corpus)
    drifted = json.loads(json.dumps(golden))
    drifted["metrics"]["total_failures"] += 1
    assert compare_reports(golden, json.loads(json.dumps(golden))) == []
    assert compare_reports(golden, drifted) != []


def test_cli_check_passes_and_fails(tmp_path, corpus_factory):
    database = write_corpus(tmp_path / "eval.db", [_report_session()])
    golden_path = tmp_path / "golden.json"
    assert eval_main([str(database), "--out", str(golden_path)]) == 0
    assert eval_main([str(database), "--check", str(golden_path)]) == 0
    # A changed corpus (extra contradiction scene) must break the golden.
    session = _report_session()
    session["events"].append(
        scene_event("s3", [block("dialogue", "它明明还放在提包里的。", "alice")])
    )
    other = write_corpus(tmp_path / "other.db", [session])
    assert eval_main([str(other), "--check", str(golden_path)]) == 1
