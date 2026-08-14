"""Pack-level frozen cache for opening + first-decision pre-generation.

Two data models:

* ``CachedOpening`` — the frozen opening segment (plan, draft, events, pacing)
  persisted at ``data/pack_cache/<pack_hash>/opening.json``.
* ``CachedPregen`` — a pre-generated segment for one specific choice
  (choice_id, pre_events, seg_events, plan, draft, pacing) persisted at
  ``data/pack_cache/<pack_hash>/pregen/<choice_id>.json``.

Both are ``RuntimeModel`` subclasses (frozen, ``extra="forbid"``) and fully
JSON-serializable via ``model_dump_json()`` / ``model_validate_json()``.
"""

from __future__ import annotations

from pathlib import Path

from src.story.runtime.contracts import RuntimeModel
from src.story.runtime.segment_contracts import (
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
)
from src.story.state.events import StoryEvent

# ---------------------------------------------------------------------------
# Cache data models
# ---------------------------------------------------------------------------


class CachedOpening(RuntimeModel):
    """Frozen opening segment persisted in PackCache.

    Contains the fully validated plan, draft, simulated events, and the
    pacing envelope used during generation.  At runtime the orchestrator
    loads this and skips all LLM / pacing / simulation work.

    ``judge_preapproved`` records that the semantic judge accepted this
    exact content at cache-build time; only then may the runtime skip the
    judge.  Caches built without a judge (offline ``init-pack``) carry
    ``False`` and are judged on first use like any fresh proposal.
    """

    segment_plan: SegmentPlan
    segment_draft: SegmentDraft
    seg_events: tuple[StoryEvent, ...]
    pacing: PacingEnvelope
    judge_preapproved: bool = False


class CachedPregen(RuntimeModel):
    """Pre-generated segment for a specific player choice.

    Used by both ``PackCache`` (on-disk, pack-scoped) and
    ``PreGenerationManager`` (in-memory, session-scoped).
    """

    choice_id: str
    pre_events: tuple[StoryEvent, ...]
    seg_events: tuple[StoryEvent, ...]
    segment_plan: SegmentPlan
    segment_draft: SegmentDraft
    pacing: PacingEnvelope


# ---------------------------------------------------------------------------
# PackCache — on-disk persistence
# ---------------------------------------------------------------------------


class PackCache:
    """Pack-level frozen cache for opening + first-decision pre-generation.

    Directory layout::

        <root>/<pack_hash>/
        ├── opening.json
        └── pregen/
            ├── <choice_id>.json
            └── ...

    Pack YAML changes → ``pack_hash`` changes → old cache is automatically
    ignored.  Use ``init-pack --force`` to regenerate explicitly.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _pack_dir(self, pack_hash: str) -> Path:
        return self.root / pack_hash

    # -- Opening -----------------------------------------------------------

    def has_opening(self, pack_hash: str) -> bool:
        """Return True if ``opening.json`` exists for this pack."""
        return (self._pack_dir(pack_hash) / "opening.json").exists()

    def load_opening(self, pack_hash: str) -> CachedOpening | None:
        """Load the cached opening, or ``None`` if not present."""
        path = self._pack_dir(pack_hash) / "opening.json"
        if not path.exists():
            return None
        return CachedOpening.model_validate_json(path.read_text(encoding="utf-8"))

    def save_opening(self, pack_hash: str, opening: CachedOpening) -> None:
        """Persist the opening segment to disk."""
        d = self._pack_dir(pack_hash)
        d.mkdir(parents=True, exist_ok=True)
        (d / "opening.json").write_text(opening.model_dump_json(indent=2), encoding="utf-8")

    # -- Pregen ------------------------------------------------------------

    def load_pregen(self, pack_hash: str, choice_id: str) -> CachedPregen | None:
        """Load a pre-generated segment for ``choice_id``, or ``None``."""
        path = self._pack_dir(pack_hash) / "pregen" / f"{choice_id}.json"
        if not path.exists():
            return None
        return CachedPregen.model_validate_json(path.read_text(encoding="utf-8"))

    def save_pregen(self, pack_hash: str, choice_id: str, pregen: CachedPregen) -> None:
        """Persist a pre-generated segment to disk."""
        d = self._pack_dir(pack_hash) / "pregen"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{choice_id}.json").write_text(pregen.model_dump_json(indent=2), encoding="utf-8")

    # -- Completeness check ------------------------------------------------

    def is_complete(self, pack_hash: str, choice_ids: list[str]) -> bool:
        """Return True if opening + all specified pregen files exist."""
        if not self.has_opening(pack_hash):
            return False
        return all(self.load_pregen(pack_hash, cid) is not None for cid in choice_ids)
