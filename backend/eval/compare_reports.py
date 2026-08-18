"""Render a before/after acceptance comparison from two eval report JSONs.

Usage::

    python eval/compare_reports.py eval/baseline.json \
        eval/candidate.json --out eval/compare.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _delta(after, before, higher_is_better=True):
    if after is None or before is None:
        return "n/a"
    diff = after - before
    arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "=")
    good = (diff > 0) == higher_is_better
    mark = "✓" if good or diff == 0 else "✗"
    return f"{before} → {after} ({arrow}{abs(round(diff, 4))} {mark})"


def render(before: dict, after: dict, title: str) -> str:
    bm, am = before["metrics"], after["metrics"]
    lines = [f"# {title}", "", "## 指标对比", ""]
    lines.append(
        "| 指标 | 修复前 | 修复后 | 变化 |"
    )
    lines.append("| --- | --- | --- | --- |")
    lines.append(
        f"| 失败总数/块数 | {bm['total_failures']}/{bm['block_count']} "
        f"(每块 {bm['failures_per_block']}) | {am['total_failures']}/{am['block_count']} "
        f"(每块 {am['failures_per_block']}) | "
        f"{_delta(am['failures_per_block'], bm['failures_per_block'], higher_is_better=False)} |"
    )
    lines.append(
        f"| 选择承接率 | {bm['choice_continuation_rate']} "
        f"({bm.get('selections_with_outcome', '?')}/{bm.get('selections_total', '?')}) | "
        f"{am['choice_continuation_rate']} "
        f"({am.get('selections_with_outcome', '?')}/{am.get('selections_total', '?')}) | "
        f"{_delta(am['choice_continuation_rate'], bm['choice_continuation_rate'])} |"
    )
    bd, ad = bm["blocks_per_decision"], am["blocks_per_decision"]
    lines.append(
        f"| 密度(块/决策) mean[min,max] | {bd['mean']}[{bd['min']},{bd['max']}] | "
        f"{ad['mean']}[{ad['min']},{ad['max']}] | {_delta(ad['mean'], bd['mean'])} |"
    )
    bl, al = bm["turn_latency_seconds"], am["turn_latency_seconds"]
    lines.append(
        f"| 回合延迟(s) mean[max] | {bl['mean']}[{bl['max']}] | {al['mean']}[{al['max']}] | "
        f"{_delta(al['mean'], bl['mean'], higher_is_better=False)} |"
    )
    lines += ["", "## 五类失败对比", "", "| 类别 | 修复前 | 修复后 |", "| --- | --- | --- |"]
    categories = sorted(set(bm["category_counts"]) | set(am["category_counts"]))
    for category in categories:
        lines.append(
            f"| {category} | {bm['category_counts'].get(category, 0)} "
            f"| {am['category_counts'].get(category, 0)} |"
        )
    lines += ["", "## 回归锚点(修复后)", ""]
    # ①②④⑤ are the regression categories the consensus doc pins to these
    # windows; post-fix acceptance means they do NOT recur there.
    regression = {
        "choice_continuation_miss",
        "scene_time_regression",
        "segment_repetition",
        "detail_contradiction",
    }
    for anchor in after["anchors"]:
        recurred = sorted(regression & set(anchor["found_categories"]))
        status = "✗" if recurred else "✓"
        window = anchor["sequence_range"]
        lines.append(
            f"- {status} **{anchor['name']}**（seq {window[0]}-{window[1]}）"
            f"①②④⑤ 复发：{recurred if recurred else '无'}"
        )
    counts_after = am["category_counts"]
    any_regression = {c: counts_after.get(c, 0) for c in regression if counts_after.get(c, 0)}
    lines.append(f"- 全局①②④⑤计数：{any_regression if any_regression else '0（全部未复发）'}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="修复前后对比")
    args = parser.parse_args()
    text = render(_load(args.before), _load(args.after), args.title)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
