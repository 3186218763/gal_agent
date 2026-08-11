from __future__ import annotations

import os

from tests.live.conftest import load_live_environment


def test_live_environment_loads_dotenv_only_when_enabled(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("OPENCODE_GO_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("RUN_LIVE_ZEN_TEST", raising=False)

    load_live_environment(dotenv_path)
    assert "OPENCODE_GO_API_KEY" not in os.environ

    monkeypatch.setenv("RUN_LIVE_ZEN_TEST", "1")
    load_live_environment(dotenv_path)
    assert os.environ["OPENCODE_GO_API_KEY"] == "file-key"

    monkeypatch.setenv("OPENCODE_GO_API_KEY", "process-key")
    load_live_environment(dotenv_path)
    assert os.environ["OPENCODE_GO_API_KEY"] == "process-key"
