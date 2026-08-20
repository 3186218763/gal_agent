"""Deterministic repetition defense for proposed segment content.

The text-comparison core lives here (single source of truth): the offline
eval harness imports it too.  ``segment_repetition_errors`` is the runtime
gate — draft prose is compared against the committed prose ring and any
re-run of distinctive earlier phrasing is rejected with the exact phrases
quoted, so the regeneration loop receives actionable reasons.

Proper nouns (character names, location names) legitimately recur and are
stoplisted per pack; a phrase only counts when it carries content beyond
function words.
"""

from __future__ import annotations

import re

from src.story.script_pack.models import CompiledScriptPack
from src.story.state import SessionState

from .segment_contracts import SegmentDraft

# ---------------------------------------------------------------------------
# Shared text utilities (used by the offline eval harness as well)
# ---------------------------------------------------------------------------

_PUNCTUATION = re.compile(r"[，。！？；：、…—\-「」『』“”\"'（）()\[\]·~,\.!?;:'\"]")
_WHITESPACE = re.compile(r"\s+")


def strip_punctuation(text: str) -> str:
    """Collapse whitespace to single spaces, then drop punctuation.

    Spaces are normalized rather than stripped so latin word boundaries
    survive: two English texts sharing a template must not fuse into one
    giant "phrase" once their spaces disappear.
    """
    return _PUNCTUATION.sub("", _WHITESPACE.sub(" ", text))


def maximal_common_substrings(a: str, b: str, min_length: int) -> list[str]:
    """Greedy left-to-right maximal shared runs of ``a`` and ``b``.

    At each position of ``a``, extend the longest run that also occurs in
    ``b`` starting from some equal character, keep it when long enough, and
    skip past it — deterministic and linear-ish on these short texts.
    """
    matches: list[str] = []
    i = 0
    while i < len(a):
        best_len = 0
        for j in range(len(b)):
            if b[j] != a[i]:
                continue
            length = 1
            while i + length < len(a) and j + length < len(b) and a[i + length] == b[j + length]:
                length += 1
            best_len = max(best_len, length)
        if best_len >= min_length:
            matches.append(a[i : i + best_len])
            i += best_len
        else:
            i += 1
    seen: set[str] = set()
    unique: list[str] = []
    for match in matches:
        if match not in seen:
            seen.add(match)
            unique.append(match)
    return unique


def shared_phrases(a: str, b: str, min_length: int = 3) -> list[str]:
    """Distinctive shared substrings between two stripped texts."""
    return maximal_common_substrings(strip_punctuation(a), strip_punctuation(b), min_length)


# Connective-only phrases ("在我和", "在这个") are shared by any two texts;
# a phrase only counts when it carries content characters.
_FUNCTION_CHARS = set(
    "的了是在这和不有人我你他她它们个着就也都还很更被把向从对与以及自己什么怎样地得过"
)


def distinctive(phrases: list[str], stoplist: tuple[str, ...] = ()) -> list[str]:
    """Drop phrases containing a stoplisted term or made of pure function words."""
    kept: list[str] = []
    for phrase in phrases:
        if any(term in phrase for term in stoplist):
            continue
        if all(char in _FUNCTION_CHARS for char in phrase):
            continue
        kept.append(phrase)
    return kept


# ---------------------------------------------------------------------------
# Runtime gate
# ---------------------------------------------------------------------------

# The deterministic gate catches wholesale re-performance only: a draft
# whose content is mostly re-run committed text, or one that re-runs a full
# clause verbatim.  Finer re-performance (a repeated gesture, re-narrated
# beat) is the Semantic Judge's job — it sees the prose window and blocks
# on the ``repetition`` kind.
_WHOLESALE_PHRASE = 24
_COVERAGE_MIN = 0.5
_QUOTED_LIMIT = 4
# 3-char runs collide by chance between any two texts (random identifiers,
# hex fragments); 5-char runs are durable evidence of re-written prose.
_MIN_PHRASE = 5


def _coverage_ratio(draft_text: str, phrases: list[str]) -> float:
    """Fraction of the draft's content characters covered by shared phrases."""
    stripped = strip_punctuation(draft_text)
    if not stripped:
        return 0.0
    spans: list[tuple[int, int]] = []
    for phrase in sorted(phrases, key=len, reverse=True):
        needle = strip_punctuation(phrase)
        if not needle:
            continue
        start = 0
        while True:
            index = stripped.find(needle, start)
            if index < 0:
                break
            spans.append((index, index + len(needle)))
            start = index + len(needle)
    spans.sort(key=lambda span: -span[1])
    occupied: list[tuple[int, int]] = []
    matched = 0
    for start, end in spans:
        if any(start < oend and end > ostart for ostart, oend in occupied):
            continue
        occupied.append((start, end))
        matched += end - start
    return matched / len(stripped)


def _pack_stoplist(pack: CompiledScriptPack) -> tuple[str, ...]:
    """Proper nouns that legitimately recur: names, locations, premise props."""
    source = pack.source
    terms = [character.name for character in source.characters]
    terms.append(source.protagonist.name)
    world = getattr(source, "world_setting", None) or getattr(source, "world", None)
    terms.extend(location.name for location in getattr(world, "locations", ()))
    return tuple(term for term in terms if term)


def draft_repetition_phrases(
    pack: CompiledScriptPack,
    committed_texts: list[str],
    draft: SegmentDraft,
) -> list[str]:
    """Distinctive phrases the draft re-runs from the committed prose."""
    if not committed_texts:
        return []
    draft_text = "\n".join(
        block.text for scene in draft.scene_drafts for block in scene.blocks
    )
    if not draft_text.strip():
        return []
    stoplist = _pack_stoplist(pack)
    found: list[str] = []
    for committed in committed_texts:
        found.extend(distinctive(shared_phrases(draft_text, committed, _MIN_PHRASE), stoplist))
    ordered: list[str] = []
    seen: set[str] = set()
    for phrase in sorted(found, key=len, reverse=True):
        if phrase not in seen:
            seen.add(phrase)
            ordered.append(phrase)
    return ordered


def segment_repetition_errors(
    pack: CompiledScriptPack,
    state: SessionState,
    draft: SegmentDraft,
) -> list[str]:
    """Reject a draft that wholesale re-runs committed prose.

    Returns actionable rejection reasons (issue-12 pattern): the exact
    repeated phrases are quoted so the regeneration loop can rewrite them
    instead of re-rolling the whole segment blindly.
    """
    committed = [record.text for record in state.recent_prose_blocks]
    phrases = draft_repetition_phrases(pack, committed, draft)
    if not phrases:
        return []
    long_phrases = [phrase for phrase in phrases if len(phrase) >= _WHOLESALE_PHRASE]
    if not long_phrases:
        draft_text = "\n".join(
            block.text for scene in draft.scene_drafts for block in scene.blocks
        )
        if _coverage_ratio(draft_text, phrases) < _COVERAGE_MIN:
            return []
    flagged = long_phrases or phrases
    quoted = "、".join(flagged[:_QUOTED_LIMIT])
    return [
        (
            f"draft re-runs phrasing the player already read ({quoted}); "
            "write this moment with new sentences — never re-narrate or paraphrase committed prose"
        )
    ]


__all__ = [
    "distinctive",
    "draft_repetition_phrases",
    "maximal_common_substrings",
    "segment_repetition_errors",
    "shared_phrases",
    "strip_punctuation",
]
