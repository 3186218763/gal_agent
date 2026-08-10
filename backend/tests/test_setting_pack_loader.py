# backend/tests/test_setting_pack_loader.py
from pathlib import Path
import pytest
from src.content.setting_pack_loader import load_setting_pack


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_load_chapter_01_pack():
    pack = load_setting_pack(SCRIPTS, "chapter_01")
    assert pack.pack_id == "chapter_01"
    assert len(pack.characters) >= 2
    assert len(pack.goals) >= 2
    assert len(pack.endings) >= 3
    assert any(e.type.value == "fallback" or "steps" in e.condition for e in pack.endings)


def test_missing_pack_raises():
    with pytest.raises(FileNotFoundError):
        load_setting_pack(SCRIPTS, "no_such_pack")
