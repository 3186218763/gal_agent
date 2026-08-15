"""Offline evaluation CLI.

Usage (from ``backend/``)::

    python -m src.story.eval data/story-v2.db --out eval/baseline.json --md eval/baseline.md
    python -m src.story.eval data/story-v2.db --check eval/baseline.json

Reads the event store read-only, prints the deterministic report JSON to
stdout (or ``--out``), optionally writes a markdown rendering (``--md``),
and compares against a golden report (``--check``, exit 1 on any drift).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.story.eval.corpus import load_corpus
from src.story.eval.report import build_report, compare_reports, render_markdown


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.story.eval")
    parser.add_argument("database", type=Path, help="path to a story-v2 SQLite event store")
    parser.add_argument("--session", action="append", help="restrict to a session id (repeatable)")
    parser.add_argument("--out", type=Path, help="write the report JSON here")
    parser.add_argument("--md", type=Path, help="write a markdown rendering here")
    parser.add_argument("--check", type=Path, help="compare against a golden report")
    parser.add_argument("--title", default="剧本质量评测报告", help="markdown title")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    corpus = load_corpus(args.database, args.session)
    if not corpus.sessions:
        print("no sessions found", file=sys.stderr)
        return 2
    report = build_report(corpus)

    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(render_markdown(report, args.title) + "\n", encoding="utf-8")

    if args.check:
        golden = json.loads(args.check.read_text(encoding="utf-8"))
        differences = compare_reports(golden, report)
        if differences:
            print(f"golden mismatch ({len(differences)} differences):", file=sys.stderr)
            for difference in differences[:40]:
                print(f"  {difference}", file=sys.stderr)
            return 1
        print("golden check passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
