import json
from pathlib import Path

from src.story.cli import main

PACK_DIR = Path(__file__).resolve().parents[1] / "script_packs" / "cafe_mystery"


def test_validate_command_prints_compiled_summary(capsys):
    assert main(["validate", str(PACK_DIR)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["pack_id"] == "cafe_mystery"
    assert len(output["pack_hash"]) == 64
    assert output["completion_requirements"] >= 2


def test_init_and_inspect_session(tmp_path: Path, capsys):
    database = tmp_path / "story.db"
    assert (
        main(
            [
                "init-session",
                str(PACK_DIR),
                "--database",
                str(database),
                "--session-id",
                "cli_session",
                "--seed",
                "17",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["revision"] == 0
    assert main(["inspect-session", "cli_session", "--database", str(database)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["session_id"] == "cli_session"
    assert output["pack_id"] == "cafe_mystery"
    assert output["phase"] == "opening"


def test_validate_missing_pack_returns_nonzero(tmp_path: Path, capsys):
    assert main(["validate", str(tmp_path / "missing")]) == 2
    assert "script pack file not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# init-pack tests (use fakes, no real LLM calls)
# ---------------------------------------------------------------------------


def test_init_pack_creates_opening_cache_files(tmp_path: Path):
    """_init_pack generates only the opening segment and saves it to PackCache.

    Offline cache tooling must not write pre-generated consequences or any
    implicit-success result — the authoritative flow is the only way a
    consequence can be committed.
    """
    import asyncio

    from src.story.cli import _init_pack
    from src.story.runtime.pack_cache import PackCache
    from src.story.runtime.unified_segment import UnifiedSegmentOutput
    from src.story.script_pack import compile_script_pack
    from tests.fakes import FakeDirector, FakeGuard, FakeSegmentWriter

    class FakeOpeningAgent:
        async def generate(self, pack, state, pacing):
            director = FakeDirector()
            writer = FakeSegmentWriter()
            plan = await director.plan_segment(pack, state, pacing)
            draft = await writer.write_segment(pack, state, plan)
            return UnifiedSegmentOutput(segment_plan=plan, segment_draft=draft)

    pack = compile_script_pack(PACK_DIR)
    cache_root = tmp_path / "pack_cache"

    result = asyncio.run(
        _init_pack(
            pack=pack,
            cache_root=cache_root,
            opening_agent=FakeOpeningAgent(),
            guard=FakeGuard(),
        )
    )

    assert result["status"] == "initialized"
    assert result["pack_hash"] == pack.pack_hash
    assert result["opening_segment_id"]
    assert "pregen_count" not in result

    # Only the opening is cached — never a choice pregen.
    cache = PackCache(cache_root)
    assert cache.has_opening(pack.pack_hash)
    assert cache.load_opening(pack.pack_hash) is not None
    assert not list(cache_root.glob(f"{pack.pack_hash}/pregen/*"))


def test_init_pack_idempotent_skips_existing_opening(tmp_path: Path):
    """Second init-pack run detects the existing opening and skips."""
    import asyncio

    from src.story.cli import _init_pack
    from src.story.runtime.pack_cache import PackCache
    from src.story.runtime.unified_segment import UnifiedSegmentOutput
    from src.story.script_pack import compile_script_pack
    from tests.fakes import FakeDirector, FakeGuard, FakeSegmentWriter

    calls = []

    class FakeOpeningAgent:
        async def generate(self, pack, state, pacing):
            calls.append(1)
            director = FakeDirector()
            writer = FakeSegmentWriter()
            plan = await director.plan_segment(pack, state, pacing)
            draft = await writer.write_segment(pack, state, plan)
            return UnifiedSegmentOutput(segment_plan=plan, segment_draft=draft)

    pack = compile_script_pack(PACK_DIR)
    cache_root = tmp_path / "pack_cache"

    first = asyncio.run(
        _init_pack(
            pack=pack,
            cache_root=cache_root,
            opening_agent=FakeOpeningAgent(),
            guard=FakeGuard(),
        )
    )
    second = asyncio.run(
        _init_pack(
            pack=pack,
            cache_root=cache_root,
            opening_agent=FakeOpeningAgent(),
            guard=FakeGuard(),
        )
    )

    assert first["status"] == "initialized"
    assert second["status"] == "already_initialized"
    assert len(calls) == 1
    assert PackCache(cache_root).has_opening(pack.pack_hash)


def test_init_pack_force_regenerates_opening(tmp_path: Path):
    """--force regenerates the opening even when one is already cached."""
    import asyncio

    from src.story.cli import _init_pack
    from src.story.runtime.pack_cache import PackCache
    from src.story.runtime.unified_segment import UnifiedSegmentOutput
    from src.story.script_pack import compile_script_pack
    from tests.fakes import FakeDirector, FakeGuard, FakeSegmentWriter

    calls = []

    class FakeOpeningAgent:
        async def generate(self, pack, state, pacing):
            calls.append(1)
            director = FakeDirector()
            writer = FakeSegmentWriter()
            plan = await director.plan_segment(pack, state, pacing)
            draft = await writer.write_segment(pack, state, plan)
            return UnifiedSegmentOutput(segment_plan=plan, segment_draft=draft)

    pack = compile_script_pack(PACK_DIR)
    cache_root = tmp_path / "pack_cache"

    asyncio.run(
        _init_pack(
            pack=pack,
            cache_root=cache_root,
            opening_agent=FakeOpeningAgent(),
            guard=FakeGuard(),
        )
    )
    asyncio.run(
        _init_pack(
            pack=pack,
            cache_root=cache_root,
            opening_agent=FakeOpeningAgent(),
            guard=FakeGuard(),
            force=True,
        )
    )

    assert len(calls) == 2
    assert PackCache(cache_root).has_opening(pack.pack_hash)


def test_init_pack_judge_approval_is_stamped_into_cache(tmp_path: Path):
    """A judge-approved opening is persisted with judge_preapproved=True."""
    import asyncio

    from src.story.cli import _init_pack
    from src.story.runtime.pack_cache import PackCache
    from src.story.runtime.semantic_judge import JudgeFindings
    from src.story.runtime.unified_segment import UnifiedSegmentOutput
    from src.story.script_pack import compile_script_pack
    from tests.fakes import FakeDirector, FakeGuard, FakeSegmentWriter

    class FakeOpeningAgent:
        async def generate(self, pack, state, pacing):
            director = FakeDirector()
            writer = FakeSegmentWriter()
            plan = await director.plan_segment(pack, state, pacing)
            draft = await writer.write_segment(pack, state, plan)
            return UnifiedSegmentOutput(segment_plan=plan, segment_draft=draft)

    class PassingJudge:
        async def judge_segment(self, pack, state, plan, draft, pending_choice=None):
            return JudgeFindings()

    pack = compile_script_pack(PACK_DIR)
    cache_root = tmp_path / "pack_cache"

    result = asyncio.run(
        _init_pack(
            pack=pack,
            cache_root=cache_root,
            opening_agent=FakeOpeningAgent(),
            guard=FakeGuard(),
            semantic_judge=PassingJudge(),
        )
    )

    assert result["status"] == "initialized"
    cached = PackCache(cache_root).load_opening(pack.pack_hash)
    assert cached is not None and cached.judge_preapproved is True


def test_init_pack_rejected_by_judge_writes_no_cache(tmp_path: Path):
    """A judge-rejected opening is never cached."""
    import asyncio

    import pytest

    from src.story.cli import _init_pack
    from src.story.runtime.pack_cache import PackCache
    from src.story.runtime.semantic_judge import JudgeFinding, JudgeFindings
    from src.story.runtime.unified_segment import UnifiedSegmentOutput
    from src.story.script_pack import compile_script_pack
    from tests.fakes import FakeDirector, FakeGuard, FakeSegmentWriter

    class FakeOpeningAgent:
        async def generate(self, pack, state, pacing):
            director = FakeDirector()
            writer = FakeSegmentWriter()
            plan = await director.plan_segment(pack, state, pacing)
            draft = await writer.write_segment(pack, state, plan)
            return UnifiedSegmentOutput(segment_plan=plan, segment_draft=draft)

    class RejectingJudge:
        async def judge_segment(self, pack, state, plan, draft, pending_choice=None):
            return JudgeFindings(
                findings=(
                    JudgeFinding(
                        kind="canon_contradiction",
                        severity="blocking",
                        detail="opening contradicts pack canon",
                    ),
                )
            )

    pack = compile_script_pack(PACK_DIR)
    cache_root = tmp_path / "pack_cache"

    with pytest.raises(RuntimeError, match="semantic judge rejected opening segment"):
        asyncio.run(
            _init_pack(
                pack=pack,
                cache_root=cache_root,
                opening_agent=FakeOpeningAgent(),
                guard=FakeGuard(),
                semantic_judge=RejectingJudge(),
            )
        )

    assert not PackCache(cache_root).has_opening(pack.pack_hash)
