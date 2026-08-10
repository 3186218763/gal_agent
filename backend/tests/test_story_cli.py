import json
from pathlib import Path

from src.story.cli import main

PACK_DIR = Path(__file__).resolve().parents[1] / "script_packs" / "cafe_mystery"


def test_validate_command_prints_compiled_summary(capsys):
    assert main(["validate", str(PACK_DIR)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["pack_id"] == "cafe_mystery"
    assert len(output["pack_hash"]) == 64
    assert output["normal_endings"] >= 3
    assert output["fallback_endings"] >= 1


def test_init_and_inspect_session(tmp_path: Path, capsys):
    database = tmp_path / "story.db"
    assert main(["init-session", str(PACK_DIR), "--database", str(database), "--session-id", "cli_session", "--seed", "17"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 0
    assert main(["inspect-session", "cli_session", "--database", str(database)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["session_id"] == "cli_session"
    assert output["pack_id"] == "cafe_mystery"
    assert output["phase"] == "opening"


def test_validate_missing_pack_returns_nonzero(tmp_path: Path, capsys):
    assert main(["validate", str(tmp_path / "missing")]) == 2
    assert "script pack file not found" in capsys.readouterr().err
