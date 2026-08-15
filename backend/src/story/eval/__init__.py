"""Offline script-quality evaluation harness.

Reads a ``story-v2`` event store from outside the engine boundary (plain
SQLite + JSON, no runtime imports) and produces a deterministic failure
report: five failure categories, choice-continuation rate, repetition rate,
option density, and coarse turn latency.  Same input, same output — the
report is a golden snapshot that prompt changes can be compared against.

See ``docs/2026-08-15-script-consensus.md`` for the category definitions and
the seed-corpus evidence the detectors are calibrated on.
"""

from src.story.eval.corpus import Corpus, SessionCorpus, load_corpus
from src.story.eval.detectors import (
    CATEGORIES,
    detect_choice_continuation_misses,
    detect_detail_contradictions,
    detect_quote_style_breaks,
    detect_scene_time_regressions,
    detect_segment_repetitions,
)
from src.story.eval.report import build_report, compare_reports, render_markdown

__all__ = [
    "CATEGORIES",
    "Corpus",
    "SessionCorpus",
    "build_report",
    "compare_reports",
    "detect_choice_continuation_misses",
    "detect_detail_contradictions",
    "detect_quote_style_breaks",
    "detect_scene_time_regressions",
    "detect_segment_repetitions",
    "load_corpus",
    "render_markdown",
]
