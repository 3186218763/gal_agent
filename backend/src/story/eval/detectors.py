"""Deterministic detectors for the five failure categories.

Every detector is pure text/structure analysis over the loaded corpus — no
model calls, no randomness, same input → same findings.  Thresholds are
calibrated on a legacy seed corpus (two sessions, 69 blocks, 9
manually-identified failures).
They are heuristic anchors for before/after comparison, not absolute truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.story.eval.corpus import (
    CATEGORIES,
    Corpus,
    Scene,
    Segment,
    Selection,
    SessionCorpus,
)

# ---------------------------------------------------------------------------
# Failure record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Failure:
    category: str
    session_id: str
    detail: str
    evidence_sequences: tuple[int, ...]
    evidence_text: tuple[str, ...] = ()
    subkind: str | None = None

    def as_dict(self) -> dict:
        record = {
            "category": self.category,
            "category_label": CATEGORIES[self.category],
            "session_id": self.session_id,
            "detail": self.detail,
            "evidence_sequences": list(self.evidence_sequences),
            "evidence_text": list(self.evidence_text),
        }
        if self.subkind is not None:
            record["subkind"] = self.subkind
        return record


def _snippet(text: str, limit: int = 48) -> str:
    cleaned = text.strip().replace("\n", " ")
    return cleaned[: limit - 1] + "…" if len(cleaned) > limit else cleaned


# ---------------------------------------------------------------------------
# Shared text utilities
# ---------------------------------------------------------------------------

_PUNCTUATION = re.compile(r"[，。！？；：、…—\-\s「」『』“”\"'（）()\[\]·~,\.!?;:'\"]")


def _strip_punctuation(text: str) -> str:
    return _PUNCTUATION.sub("", text)


def _maximal_common_substrings(a: str, b: str, min_length: int) -> list[str]:
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
            while (
                i + length < len(a) and j + length < len(b) and a[i + length] == b[j + length]
            ):
                length += 1
            best_len = max(best_len, length)
        if best_len >= min_length:
            matches.append(a[i : i + best_len])
            i += best_len
        else:
            i += 1
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for match in matches:
        if match not in seen:
            seen.add(match)
            unique.append(match)
    return unique


def shared_phrases(a: str, b: str, min_length: int = 3) -> list[str]:
    """Distinctive shared substrings between two stripped texts."""
    return _maximal_common_substrings(_strip_punctuation(a), _strip_punctuation(b), min_length)


# Phrases that recur legitimately (setting, names, props) and must not count
# as repetition or formula evidence.
_PHRASE_STOPLIST = (
    "艾丽",
    "丽丝",
    "鲍勃",
    "美奈",
    "店长",
    "街角",
    "咖啡",
    "笔记本",
    "笔记",
    "背包",
    "提包",
    "橱窗",
    "托盘",
)

_STOPFILTER = re.compile("|".join(_PHRASE_STOPLIST))

# Connective-only phrases ("在我和", "在这个") are shared by any two texts;
# a phrase only counts when it carries content characters.
_FUNCTION_CHARS = set(
    "的了是在这和不有人我你他她它们个着就也都还很更被把向从对与以及自己什么怎样地得过"
)


def distinctive(phrases: list[str]) -> list[str]:
    """Drop phrases containing a stoplisted term or made of pure function words."""
    kept: list[str] = []
    for phrase in phrases:
        if _STOPFILTER.search(phrase):
            continue
        if all(char in _FUNCTION_CHARS for char in phrase):
            continue
        kept.append(phrase)
    return kept


# ---------------------------------------------------------------------------
# ① 选项后果未兑现 (choice continuation miss)
# ---------------------------------------------------------------------------

# A choice whose label promises a concrete physical action needs matching
# narration evidence: search compounds must appear in the post-choice
# segment's narration (dialogue alone — a character merely *talking about*
# finding — does not execute the pledge; bare 寻/找 characters match
# 寻常/寻找-adjacent noise, so only compounds count).
_PLEDGE_VERBS = re.compile(r"找找|一起找|寻找|搜寻|翻找|搜找|查找")
_PLEDGE_NARRATION_HINTS = re.compile(r"翻找|寻找|搜寻|搜找|查找|找找|一起找")

# A character re-issuing a "you are an uninvolved stranger" warning after
# the player has already committed choices treats the player as a passerby
# — the engagement of every prior choice was reset.
_STRANGER_ADDRESS = re.compile(r"这位朋友|这位客人|不相识的|无关的人|初见者|陌生的")
_STRANGER_WARNING = re.compile(r"卷入|打听|不要多|最好不|别管|离开|回避|少管")

# A target character that answers with lines mostly recycled from its own
# earlier lines is a formulaic response, not a carried-out choice.
_FORMULAIC_RATIO = 0.15
_FORMULAIC_MATCH_MIN = 3


def _formulaic_ratio(lines: list[str], prior_lines: list[str]) -> float:
    """Fraction of the target's post-choice text covered by recycled phrasing."""
    if not lines:
        return 0.0
    total = 0
    matched = 0
    for line in lines:
        stripped = _strip_punctuation(line)
        total += len(stripped)
        claimed: list[tuple[int, int]] = []
        for prior in prior_lines:
            for phrase in distinctive(shared_phrases(line, prior, _FORMULAIC_MATCH_MIN)):
                start = 0
                while True:
                    index = stripped.find(phrase, start)
                    if index < 0:
                        break
                    claimed.append((index, len(phrase)))
                    start = index + 1
        # Greedy non-overlapping cover by longest match first.
        claimed.sort(key=lambda span: -span[1])
        occupied: list[tuple[int, int]] = []
        for start, length in claimed:
            if any(start < end and start + length > begin for begin, end in occupied):
                continue
            occupied.append((start, start + length))
            matched += length
    return matched / total if total else 0.0


def _post_choice_segment(session: SessionCorpus, selection: Selection) -> Segment | None:
    return next(
        (
            segment
            for segment in session.segments
            if min(segment.sequences) > selection.sequence
        ),
        None,
    )


def _prior_target_lines(session: SessionCorpus, selection: Selection) -> list[str]:
    if selection.target_character_id is None:
        return []
    return [
        block.text
        for scene in session.scenes
        if scene.sequence < selection.sequence
        for block in scene.blocks
        if block.kind == "dialogue" and block.character_id == selection.target_character_id
    ]


def detect_choice_continuation_misses(session: SessionCorpus) -> list[Failure]:
    """① A committed choice whose consequence segment ignores it.

    Checked per selection; first matching signal fails it:
    - ``target_absent``: the chosen target never appears in the segment.
    - ``formulaic_target``: the target's post-choice dialogue is mostly
      recycled from its own earlier lines (the scene re-runs the character's
      formula instead of responding to the player's probe).
    - ``engagement_reset``: some character re-issues an "uninvolved stranger"
      warning at the player, resetting the engagement every prior committed
      choice established.
    - ``pledge_unexecuted``: the choice label promises a concrete action
      (e.g. search together) but the post-choice narration never executes it.
    """
    failures: list[Failure] = []
    for selection in session.selections:
        segment = _post_choice_segment(session, selection)
        if segment is None:
            continue  # no content followed the choice (session stopped)
        target = selection.target_character_id or ""
        lines = segment.dialogue_lines(target) if target else []

        if target and not lines and target not in _present_character_ids(segment):
            failures.append(
                Failure(
                    category="choice_continuation_miss",
                    session_id=session.session_id,
                    subkind="target_absent",
                    detail=f"选择 {selection.option_id} 指向 {target},但后继段落中该角色完全缺席",
                    evidence_sequences=(selection.sequence, *segment.sequences),
                    evidence_text=(f"label: {selection.label}",),
                )
            )
            continue

        prior = _prior_target_lines(session, selection)
        ratio = _formulaic_ratio(lines, prior)
        if lines and prior and ratio >= _FORMULAIC_RATIO:
            failures.append(
                Failure(
                    category="choice_continuation_miss",
                    session_id=session.session_id,
                    subkind="formulaic_target",
                    detail=(
                        f"选择 {selection.option_id}(target={target})后,该角色台词"
                        f"{ratio:.0%} 由旧台词回收拼成,是公式化应答而非对选择的承接"
                    ),
                    evidence_sequences=(selection.sequence, *segment.sequences),
                    evidence_text=(
                        f"label: {selection.label}",
                        *(f"{target}: {_snippet(line)}" for line in lines),
                    ),
                )
            )
            continue

        reset_line = next(
            (
                block.text
                for scene in segment.scenes
                for block in scene.blocks
                if block.kind == "dialogue"
                and _STRANGER_ADDRESS.search(block.text)
                and _STRANGER_WARNING.search(block.text)
            ),
            None,
        )
        if reset_line is not None:
            failures.append(
                Failure(
                    category="choice_continuation_miss",
                    session_id=session.session_id,
                    subkind="engagement_reset",
                    detail=(
                        f"选择 {selection.option_id} 之后仍有角色把玩家当作无关路人"
                        "劝退,玩家的既有介入被重置"
                    ),
                    evidence_sequences=(selection.sequence, *segment.sequences),
                    evidence_text=(f"label: {selection.label}", _snippet(reset_line)),
                )
            )
            continue

        if _PLEDGE_VERBS.search(selection.label) and not _PLEDGE_NARRATION_HINTS.search(
            segment.narration_text()
        ):
            failures.append(
                Failure(
                    category="choice_continuation_miss",
                    session_id=session.session_id,
                    subkind="pledge_unexecuted",
                    detail=(
                        f"选择 {selection.option_id} 承诺了具体行动,但后继段落的叙述"
                        "中没有任何执行迹象(仅台词提及不算)"
                    ),
                    evidence_sequences=(selection.sequence, *segment.sequences),
                    evidence_text=(f"label: {selection.label}",),
                )
            )
    return failures


def _present_character_ids(segment: Segment) -> set[str]:
    return {
        character_id
        for scene in segment.scenes
        for character_id in scene.present_character_ids
    }


# ---------------------------------------------------------------------------
# ② 场景跳变 (scene time regression)
# ---------------------------------------------------------------------------

# Time-of-day mentions mapped to a monotonic day ordinal.  Only narration
# describing the *current* scene time counts; deadline references ("傍晚六点
# 打烊") and hypotheticals ("天黑之后") are excluded via the suffix rule.
_TIME_ORDINALS: tuple[tuple[str, int], ...] = (
    ("清晨", 1),
    ("早晨", 1),
    ("早上", 1),
    ("上午", 2),
    ("正午", 3),
    ("中午", 3),
    ("午后", 3),
    ("下午", 3),
    ("夕阳", 4),
    ("日落", 4),
    ("黄昏", 5),
    ("傍晚", 5),
    ("天色渐暗", 5),
    ("暮色", 5),
    ("天黑", 6),
    ("夜晚", 6),
    ("夜幕", 6),
    ("深夜", 7),
)
_DEADLINE_SUFFIX = re.compile(r".{0,8}(打烊|闭店|之前|之后|以前|以后)")


def _scene_time_ordinal(scene: Scene, blocks: slice) -> int | None:
    """Max current-time ordinal over the given blocks; None if no signal."""
    ordinal: int | None = None
    selected = scene.blocks[blocks]
    for block in selected:
        for token, value in _TIME_ORDINALS:
            start = 0
            while True:
                index = block.text.find(token, start)
                if index < 0:
                    break
                window = block.text[max(0, index - 2) : index + len(token) + 8]
                if not _DEADLINE_SUFFIX.match(window):
                    ordinal = value if ordinal is None else max(ordinal, value)
                start = index + 1
    return ordinal


def detect_scene_time_regressions(session: SessionCorpus) -> list[Failure]:
    """② A scene that opens at an earlier time of day than the story reached."""
    failures: list[Failure] = []
    for previous, current in zip(session.scenes, session.scenes[1:]):
        reached = _scene_time_ordinal(previous, slice(None))
        opening = _scene_time_ordinal(current, slice(0, 2))
        if reached is None or opening is None or opening >= reached:
            continue
        failures.append(
            Failure(
                category="scene_time_regression",
                session_id=session.session_id,
                detail=(
                    f"上一步故事时间已到 {reached} 级,下一场开场却回到 {opening} 级"
                    "(时间倒流无过渡)"
                ),
                evidence_sequences=(previous.sequence, current.sequence),
                evidence_text=(
                    _snippet(previous.text),
                    _snippet(current.blocks[0].text if current.blocks else current.text),
                ),
            )
        )
    return failures


# ---------------------------------------------------------------------------
# ③ 语气断裂 (quote style break)
# ---------------------------------------------------------------------------

# A session-level typography stability check: the dialogue quote style may
# not change between segments (the writer cannot see its previous segment's
# layout, so any change mid-session reads as a voice break).
_QUOTE_STYLES: tuple[tuple[str, str], ...] = (
    ("corner", "「"),
    ("curly", "“"),
)


def _block_quote_style(text: str) -> str:
    for name, marker in _QUOTE_STYLES:
        if marker in text:
            return name
    return "none"


def _segment_quote_style(segment: Segment) -> str:
    styles = [
        _block_quote_style(block.text)
        for scene in segment.scenes
        for block in scene.blocks
        if block.kind == "dialogue"
    ]
    if not styles:
        return "none"
    # Dominant style; a mixed segment counts as mixed only if truly split.
    counts: dict[str, int] = {}
    for style in styles:
        counts[style] = counts.get(style, 0) + 1
    return max(sorted(counts), key=lambda style: (counts[style], style))


def detect_quote_style_breaks(session: SessionCorpus) -> list[Failure]:
    """③ Quote format drifting between segments within one session."""
    failures: list[Failure] = []
    transitions: list[tuple[Segment, Segment, str, str]] = []
    for previous, current in zip(session.segments, session.segments[1:]):
        old, new = _segment_quote_style(previous), _segment_quote_style(current)
        if old != new:
            transitions.append((previous, current, old, new))
    if not transitions:
        return failures
    first = transitions[0]
    previous, current, old, new = first
    failures.append(
        Failure(
            category="quote_style_break",
            session_id=session.session_id,
            detail=(
                f"会话内引号排版漂移:段{previous.index}({old}) → 段{current.index}({new})"
                f",共 {len(transitions)} 处变化"
            ),
            evidence_sequences=(
                *previous.sequences[-1:],
                *current.sequences[:1],
            ),
            evidence_text=(
                f"segment {previous.index} style: {old}",
                f"segment {current.index} style: {new}",
            ),
        )
    )
    return failures


# ---------------------------------------------------------------------------
# ④ 整段复读 (segment repetition)
# ---------------------------------------------------------------------------

# A session-level repetition check: some later segment re-runs distinctive
# phrasing from earlier segments (formula lines, repeated gestures).
_REPETITION_PHRASE_MIN = 2
_REPETITION_LONG_PHRASE = 6


def _repeated_phrases(segment: Segment, earlier: list[Segment]) -> list[str]:
    found: set[str] = set()
    for prior in earlier:
        for phrase in distinctive(shared_phrases(segment.text, prior.text)):
            found.add(phrase)
    return sorted(found)


def flagged_repetition_segments(session: SessionCorpus) -> list[tuple[Segment, list[str]]]:
    """Segments (with their repeated phrases) that re-run earlier phrasing."""
    flagged: list[tuple[Segment, list[str]]] = []
    for index, segment in enumerate(session.segments):
        if index == 0:
            continue
        phrases = _repeated_phrases(segment, list(session.segments[:index]))
        long_phrases = [phrase for phrase in phrases if len(phrase) >= _REPETITION_LONG_PHRASE]
        if len(phrases) >= _REPETITION_PHRASE_MIN or long_phrases:
            flagged.append((segment, phrases))
    return flagged


def detect_segment_repetitions(session: SessionCorpus) -> list[Failure]:
    """④ A later segment recycling distinctive phrases from earlier ones."""
    failures: list[Failure] = []
    flagged = flagged_repetition_segments(session)
    if not flagged:
        return failures
    first_segment, first_phrases = flagged[0]
    failures.append(
        Failure(
            category="segment_repetition",
            session_id=session.session_id,
            detail=(
                f"段{first_segment.index} 起复现前文特征短语"
                f"({', '.join(first_phrases[:4])});全会话共 {len(flagged)} 个段落命中"
            ),
            evidence_sequences=first_segment.sequences,
            evidence_text=tuple(f"repeated phrase: {phrase}" for phrase in first_phrases[:4]),
        )
    )
    return failures


# ---------------------------------------------------------------------------
# ⑤ 细节自相矛盾 (detail contradiction)
# ---------------------------------------------------------------------------

_NOTEBOOK_TERMS = re.compile(r"笔记本|硬皮本|封皮|封面|那本|本子|它")
_SENTENCE_SPLIT = re.compile(r"[。！？!?…\n]+")
_COLORS: tuple[tuple[str, str], ...] = (
    ("深蓝", "深蓝"),
    ("藏蓝", "深蓝"),
    ("墨绿", "墨绿"),
    ("酒红", "酒红"),
    ("米白", "米白"),
    ("黑色", "黑"),
    ("黑皮", "黑"),
    ("蓝色", "蓝"),
    ("深棕", "深棕"),
    ("棕色", "棕"),
    ("白色", "白"),
    ("红色", "红"),
    ("黑色", "黑"),
    ("灰色", "灰"),
)
_CONTAINER_ORDER = (
    "背包的侧袋",
    "侧袋",
    "背包",
    "提包",
    "手边",
    "口袋",
    "抽屉",
    "桌上",
    "桌下",
    "包里",
    "沙发",
    "椅子",
    "角落",
)
_LAST_SEEN_VERBS = re.compile(r"放在|还在|留在|塞在|搁在")


def _sentences(text: str) -> list[str]:
    return [s for s in (part.strip() for part in _SENTENCE_SPLIT.split(text)) if s]


def detect_detail_contradictions(session: SessionCorpus) -> list[Failure]:
    """⑤ Two committed versions of the same small fact.

    Two corpus-calibrated sub-checks:
    - ``notebook_color``: the notebook's cover color changes across scenes.
    - ``last_seen_location``: the "where I last had it" claim names different
      containers across scenes.
    """
    failures: list[Failure] = []
    color_mentions: dict[str, list[int]] = {}
    location_mentions: dict[str, list[int]] = {}
    for scene in session.scenes:
        for sentence in _sentences(scene.text):
            if not _NOTEBOOK_TERMS.search(sentence):
                continue
            for token, normalized in _COLORS:
                if token in sentence:
                    color_mentions.setdefault(normalized, []).append(scene.sequence)
                    break
            if _LAST_SEEN_VERBS.search(sentence):
                for container in _CONTAINER_ORDER:
                    if container in sentence:
                        normalized = "背包侧袋" if container == "背包的侧袋" else container
                        location_mentions.setdefault(normalized, []).append(scene.sequence)
                        break

    if len(color_mentions) >= 2:
        ordered = sorted(color_mentions.items(), key=lambda item: item[1][0])
        colors = [color for color, _ in ordered]
        failures.append(
            Failure(
                category="detail_contradiction",
                session_id=session.session_id,
                subkind="notebook_color",
                detail=f"笔记本封皮颜色前后不一:{' → '.join(colors)}",
                evidence_sequences=tuple(
                    sequence for _, sequences in ordered for sequence in sequences
                ),
                evidence_text=tuple(f"{color}: seq{sequences[0]}" for color, sequences in ordered),
            )
        )
    if len(location_mentions) >= 2:
        ordered = sorted(location_mentions.items(), key=lambda item: item[1][0])
        locations = [location for location, _ in ordered]
        failures.append(
            Failure(
                category="detail_contradiction",
                session_id=session.session_id,
                subkind="last_seen_location",
                detail=f"笔记本最后目击地点前后不一:{' → '.join(locations)}",
                evidence_sequences=tuple(
                    sequence for _, sequences in ordered for sequence in sequences
                ),
                evidence_text=tuple(
                    f"{location}: seq{sequences[0]}" for location, sequences in ordered
                ),
            )
        )
    return failures


# ---------------------------------------------------------------------------
# Corpus-level entry point
# ---------------------------------------------------------------------------


DETECTORS = (
    detect_choice_continuation_misses,
    detect_scene_time_regressions,
    detect_quote_style_breaks,
    detect_segment_repetitions,
    detect_detail_contradictions,
)


def detect_all(corpus: Corpus) -> list[Failure]:
    failures: list[Failure] = []
    for session in corpus.sessions:
        for detector in DETECTORS:
            failures.extend(detector(session))
    return failures
