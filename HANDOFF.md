# Dynamic Galgame Implementation Handoff

## Purpose

Continue the current uncommitted implementation to a coherent, verified baseline. The target is a dynamic Galgame platform in which the authored Script Pack defines the possibility space and convergence contract, the player acts only through presented choices, an Agent writes consequences in real time, and the engine preserves causality, replay consistency, and bounded completion.

Do not restart design discovery. The user has approved the current direction and asked for implementation to continue.

## Authoritative project artifacts

- Domain language: `/home/miku/szj/gal_agent/CONTEXT.md`
- Architecture decisions: `/home/miku/szj/gal_agent/docs/adr/`
- Especially relevant decisions:
  - `0002-pin-playthroughs-to-script-pack-versions.md`
  - `0003-commit-choice-meaning-before-consequences.md`
  - `0007-use-an-independent-semantic-judge.md`
  - `0008-commit-complete-replayable-segments.md`
  - `0010-bound-runtime-latency-with-replaceable-models.md`
  - `0011-require-causal-traces-for-player-impact.md`
  - `0013-commit-player-choice-before-generating-its-consequence.md`
  - `0014-use-one-authoritative-playthrough-command-flow.md`
- Current implementation is the uncommitted worktree diff against `HEAD` (`4355263`). Read it before editing.
- `README.md` and `backend/README.md` describe the older runtime in several places and are not authoritative where they conflict with the ADRs or current diff. Update them as part of cleanup.

The user intentionally deleted `PROJECT_GOAL.md`, the old `docs/superpowers/` plans/specs, and two older audit/research documents. Do not restore or recreate them. Preserve all unrelated user changes and untracked `.agents/`, `.opencode/`, `skills-lock.json`, and other files.

## Current implementation state

The worktree already implements most of the new command flow:

- `PresentedChoice` carries stable Choice Meaning fields.
- selecting a choice commits its meaning before model-dependent generation;
- failed generation leaves a durable Pending Consequence;
- `choice_id = null` resumes the same pending consequence;
- stable command receipts make concurrent/repeated recovery replay one result;
- consequence events carry the source choice event ID;
- new choices, segments, and endings are rejected while a consequence is pending;
- no placeholder/fallback fiction is committed on generation failure;
- only complete, atomically committed segments are presented as playable;
- Script Pack source versions are stored in `script_pack_versions`, recompiled on load, hash checked, and pinned to existing playthroughs;
- create/read/turn HTTP paths load the pinned version;
- `/turns` is now the production mutation route; legacy `/advance` and `/choices/{id}` routes have already been removed from `backend/src/story/api.py`;
- public projections expose only `pending_consequence_status = "awaiting_resolution"`, not internal pending IDs;
- `frontend/src/App.tsx` detects that state after refresh and resumes `/turns` with `choiceId: null`.

Focused verification was green before this handoff:

```text
backend focused tests: 37 passed
backend test_turn_orchestrator.py: 15 passed
frontend tests: 74 passed
frontend build: passed
```

Treat these as prior evidence, not a substitute for rerunning the full gates after further edits.

## Required remaining work

Complete all of the following, working from the existing diff rather than replacing it wholesale.

1. Finish the Script Pack Version and single-command-flow migration.
   - `backend/src/story/cli.py:init-session` and session creation already pass the compiled pack snapshot.
   - Rewrite `autoplay` and `play-live` wiring to use `TurnOrchestrator`, including opening, choice selection, pending-consequence recovery, and ending. It must use the same authoritative Playthrough command flow as HTTP.
   - `_init_pack` still relies on the old pregeneration/default-success path. Remove it if obsolete, or explicitly constrain it to offline cache tooling that cannot define production state transitions. Do not retain an implicit-success consequence path.

2. Remove unreachable legacy production surfaces.
   - Delete `frontend/src/streamLegacy.ts` and the `streamAdvance` re-export from `frontend/src/stream.ts` once references/tests are migrated.
   - Remove `choose` and `advanceUrl` from `frontend/src/api.ts` once callers/tests are migrated.
   - Update frontend tests that still mock `/choices/` or `/advance`.
   - Update backend tests that assert the deleted routes as production contracts. Tests specifically for an internal legacy module may remain only if the module is clearly non-production and does not weaken current invariants.
   - `RuntimeService` may temporarily remain as internal legacy code only if no API, CLI, app startup, frontend path, or production adapter uses it. Prefer deletion if dependencies can be removed cleanly.
   - Update README/API documentation so `/turns` is the only mutation path and generation failure semantics match Pending Consequence behavior.

3. Finish frontend recovery behavior and tests.
   - Add App/Playback coverage for a projection with `pending_consequence_status: "awaiting_resolution"`.
   - After a failed consequence request followed by refresh, assert exactly one `/turns` request with `choice_id: null`.
   - Assert the committed choice is not offered again and no uncommitted/provisional story block is replayed.
   - Preserve the rule that only `segment_ready` data becomes playable.

4. Harden HTTP recovery tests.
   - Prove a failed `/turns` consequence attempt leaves Pending Consequence visible through the subsequent GET projection.
   - Cover stale `expected_revision`, repeat idempotency keys, and concurrent/repeated pending recovery. The winning committed result must replay; no duplicate consequence or segment may append.
   - Keep SSE `error` behavior coherent with the durable GET projection.

5. Close the semantic integrity gaps described by the ADR baseline.
   - Wire the independent Semantic Judge into the proposed-segment acceptance path in `TurnOrchestrator`. It reports structured findings and must not write prose or mutate state.
   - Carry structured Choice Meaning -> Story Consequence causality far enough that a Causal Trace can be derived and tested, including ending relevance where applicable. Do not infer causality only from prose.
   - Keep deterministic validation authoritative and fail closed on judge/model failure; never synthesize placeholder fiction or silently default a consequence to success.

6. Bring `backend/script_packs/cafe_mystery/pack.yaml` up to the approved Engine Work standard.
   - It should exercise the domain contracts rather than act as a fixed route tree.
   - Target a normal 30-45 minute playthrough, authored open questions and dramatic obligations, bounded convergence, and meaningful completion review.
   - Preserve compiler/schema validity and add focused pack tests for its authored invariants. Do not encode a finite list of exact generated endings.

7. Run and fix all quality gates.

```bash
cd /home/miku/szj/gal_agent/backend
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests

cd /home/miku/szj/gal_agent/frontend
npm test
npm run lint
npm run build
```

Do not run live/model-network tests unless they are required to diagnose a local failure and can run without exposing credentials. Never read, print, or include `backend/.env` in output. No secret is needed for the offline acceptance gates.

## Non-negotiable invariants

- `/turns` is the one production mutation interface.
- A selected Choice Meaning is irreversible before generation begins.
- Failure preserves Pending Consequence and permits only recovery of that consequence.
- One stable consequence command is shared across devices/retries and appends at most once.
- No default-success result, placeholder story, speculative block, or incomplete segment becomes committed/playable.
- Existing Playthroughs always use their pinned immutable Script Pack Version, even if author YAML changes.
- Story truth comes from committed structured history, not summaries or prose alone.
- Normal latency goal: begin playing a committed segment within about 10 seconds; reliability and committed-only playback take precedence over streaming uncommitted prose.

## Working approach

Inspect the current diff and tests before editing. Keep the `TurnOrchestrator` interface as the production seam and test behavior through it and `/turns`. Make scoped patches with `apply_patch`; do not reset, checkout, or restore user changes. Commit only if the user explicitly requests a commit.

## Suggested skills

- `codebase-design`: keep `TurnOrchestrator` as the deep module behind the single production interface and remove parallel mutation seams.
- `tdd`: add the pending-recovery, idempotency, semantic-judge, causal-trace, and frontend refresh tests before or alongside each behavior change.
- `domain-modeling`: use when extending Story Consequence/Causal Trace structures or rewriting `cafe_mystery`; preserve the vocabulary in `CONTEXT.md`.
- `diagnosing-bugs`: use only if full-suite or race/recovery failures are non-obvious.
- `code-review`: after implementation, review the final worktree against `HEAD` and the ADRs before handoff back to the user.

## Completion report expected

Report the implementation outcome, notable interface decisions, exact offline gate results, any live tests not run, and remaining risks. Cite changed local files with absolute clickable paths. Do not call the work complete while required tests or production references to legacy mutation paths remain.
