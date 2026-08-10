# backend/tests/test_v2_only_layout.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATHS = (
    "src/agents",
    "src/content",
    "src/core",
    "src/domain",
    "src/kernel",
    "src/models",
    "src/rules",
    "src/models.py",
    "scripts/chapter_01",
)


def test_v1_runtime_paths_are_removed():
    remaining = [path for path in LEGACY_PATHS if (ROOT / path).exists()]
    assert remaining == []


def test_main_does_not_expose_v1_protocol():
    source = (ROOT / "src/main.py").read_text(encoding="utf-8")
    assert '"/api/sessions"' not in source
    assert '"/ws/game/' not in source
    assert "GAL_USE_STUBS" not in source
