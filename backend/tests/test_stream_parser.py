"""Tests for incremental JSON block stream parser."""

import json

from src.story.runtime.stream_parser import BlockStreamParser


def test_extracts_blocks_one_at_a_time():
    full = json.dumps(
        {
            "blocks": [
                {"kind": "narration", "text": "Hello world."},
                {"kind": "dialogue", "character_id": "alice", "text": "Hi there."},
            ],
            "terminal": "decision",
            "choices": [{"option_id": "a", "label": "Ask"}],
        }
    )
    parser = BlockStreamParser()
    seen = []
    chunk_size = 10
    for i in range(0, len(full), chunk_size):
        seen.extend(parser.feed(full[i : i + chunk_size]))
    assert len(seen) == 2
    assert seen[0]["kind"] == "narration"
    assert seen[0]["text"] == "Hello world."
    assert seen[1]["kind"] == "dialogue"
    assert seen[1]["character_id"] == "alice"


def test_does_not_yield_partial_blocks():
    parser = BlockStreamParser()
    assert parser.feed('{"blocks": [{"kind": "narration", "text": "') == []
    assert parser.feed("partial") == []
    result = parser.feed(' done"}]')
    assert len(result) == 1
    assert result[0]["text"] == "partial done"


def test_handles_braces_inside_strings():
    parser = BlockStreamParser()
    parser.feed('{"blocks": [{"kind": "narration", "text": "has } brace')
    result = parser.feed(' inside"}]}')
    assert len(result) == 1
    assert result[0]["text"] == "has } brace inside"


def test_handles_escaped_quotes_in_strings():
    parser = BlockStreamParser()
    parser.feed('{"blocks": [{"kind": "narration", "text": "say \\"hello\\"')
    result = parser.feed(' to her"}]}')
    assert len(result) == 1
    assert result[0]["text"] == 'say "hello" to her'


def test_finalize_returns_full_json():
    full = {
        "blocks": [{"kind": "narration", "text": "Done."}],
        "terminal": "continue",
        "choices": [],
    }
    raw = json.dumps(full)
    parser = BlockStreamParser()
    for i in range(0, len(raw), 5):
        parser.feed(raw[i : i + 5])
    result = parser.finalize()
    assert result is not None
    assert result["terminal"] == "continue"


def test_finalize_returns_none_on_invalid_json():
    parser = BlockStreamParser()
    parser.feed("not valid json at all")
    assert parser.finalize() is None


def test_empty_feed_returns_empty():
    parser = BlockStreamParser()
    assert parser.feed("") == []
