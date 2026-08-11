"""Incremental JSON parser for streaming model output.

Feeds text deltas and yields complete block dicts as they become
available in the ``blocks`` array of the streamed JSON document.
"""

from __future__ import annotations

import json


class BlockStreamParser:
    """Incrementally parses a JSON token stream to extract block objects.

    Call :meth:`feed` with each text delta.  It returns a list of
    newly-completed block dicts (may be empty).  After the stream ends,
    call :meth:`finalize` to parse the full accumulated buffer.
    """

    def __init__(self) -> None:
        self._buffer: str = ""
        self._search_pos: int | None = None  # position to resume scanning
        self._blocks_done: bool = False

    def feed(self, text: str) -> list[dict]:
        if not text:
            return []
        self._buffer += text
        if self._search_pos is None:
            marker = '"blocks"'
            idx = self._buffer.find(marker)
            if idx == -1:
                return []
            bracket = self._buffer.find("[", idx)
            if bracket == -1:
                return []
            self._search_pos = bracket + 1

        if self._blocks_done:
            return []

        results: list[dict] = []
        while True:
            block, next_pos = self._extract_next(self._search_pos)
            if block is None:
                break
            results.append(block)
            self._search_pos = next_pos
        return results

    def _extract_next(self, start: int) -> tuple[dict | None, int]:
        buf = self._buffer
        brace_pos = buf.find("{", start)
        bracket_pos = buf.find("]", start)

        if brace_pos == -1:
            return None, start
        if bracket_pos != -1 and bracket_pos < brace_pos:
            self._blocks_done = True
            return None, start

        depth = 0
        in_string = False
        escaped = False
        for i in range(brace_pos, len(buf)):
            ch = buf[i]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(buf[brace_pos : i + 1]), i + 1
                    except json.JSONDecodeError:
                        return None, i + 1
        return None, start

    def finalize(self) -> dict | None:
        """Attempt to parse the full accumulated buffer as JSON."""
        try:
            return json.loads(self._buffer)
        except json.JSONDecodeError:
            return None
