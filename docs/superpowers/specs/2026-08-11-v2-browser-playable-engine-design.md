# V2 Browser-Playable Gal Engine Design

## Status

Approved on 2026-08-11. This document defines the first backend-focused
delivery needed for a browser player to complete a real-model game session.

## Product Decisions

- The first playable browser version uses the real model to generate every
  scene. It is not an offline demo mode.
- Players select only server-presented choices. Free-text play remains out of
  scope.
- The browser restores the most recent session on the same device. There is no
  account, cross-device sync, or session library in this delivery.
- Players can explicitly start a new session. Existing sessions remain in the
  event store.
- OpenCode Go with `deepseek-v4-flash` remains the default production model.
  The existing model factory remains the provider boundary; multi-provider
  routing is not a first-delivery feature.
- The frontend is a thin player over a server-authoritative API. It uses
  temporary pixel-style backgrounds and character silhouettes. Generated or
  hand-drawn 2D art is deferred.
- The target play surface is a traditional visual novel: central character
  presentation, centered dialogue text, and vertically stacked A/B/C/D choices.

## Goals

1. Make the current V2 runtime execute real Planner, Writer, and choice
   resolution calls with the configured OpenCode Go model.
2. Preserve the kernel invariant: only deterministic validation, simulation,
   reduction, and atomic event persistence change a session.
3. Make all player mutations recoverable after retries, duplicate clicks,
   response loss, refreshes, and stale pages.
4. Expose a public session projection sufficient for a browser player without
   leaking hidden facts, candidate values, character knowledge, beliefs,
   relationship values, goals, or model context.
5. Keep normal development and CI validation offline and deterministic. Limit
   real provider calls to explicitly enabled capability tests.

## Non-Goals

- Free-text actions, Chat Completions, WebSocket streaming, and V1 compatibility.
- User accounts, authentication, cross-device saves, or deleting old sessions.
- Dynamic image generation, polished 2D sprites, audio, BGM, or a full asset
  pipeline.
- Background agents that independently write canon, memory, or world state.
- A generic multi-provider control plane. The implementation keeps a clean
  factory boundary but ships the existing OpenCode Go configuration.
- A full evaluation platform, although the design leaves a small diagnostic
  seam for a later evaluation milestone.

## Current Findings

The current runtime already has the correct authoritative core:

```text
script pack -> ContextAssembler -> Planner -> Validator -> Simulator
            -> Writer -> reducer -> revisioned SQLite event store
```

It also has offline coverage: the checked suite passes `99 passed, 1 skipped`.
The skipped test is the opt-in real-model capability test. The example
`cafe_mystery` pack compiles successfully with 3 characters, 9 facts, 3 goals,
4 normal endings, and 1 fallback ending.

Three concrete blockers were found while enabling the real-model path:

1. `tests/live` calls `OpenCodeGoSettings.from_env()` without loading `.env`.
   `src/main.py` and `play-live` do load it, so the test path disagrees with
   actual entrypoints.
2. The local resolver selected `openai-agents 0.19.4`. Its strict structured
   output schema rejects `ActionResolution.learned_facts`, currently typed as
   `dict[str, tuple[str, ...]]`. The dynamic mapping becomes JSON Schema
   `additionalProperties`, which cannot be made strict. The failure happens
   before a network request.
3. `uv.lock` is ignored and was not tracked. Dependency declarations have only
   lower bounds, so a fresh environment cannot reproduce a tested SDK set.

The current `idempotency_key` is recorded in `PlayerActionSelected` but is not
looked up before execution. A response-loss retry therefore receives a revision
conflict instead of the original result. `advance` has no idempotency key.

The current API also does not expose the public location or present characters
needed by the browser to select pixel placeholders, and a dialogue block exposes
only a character ID rather than a public name map.

## Architecture

```text
Browser Player
  -> Player Projection API
  -> Command Reliability Layer
  -> RuntimeService
       Context -> Planner -> Validator -> Simulator -> Reducer
       Writer ----------------------------------------------^
  -> StoryEventStore / SQLite
       sessions + events + command receipts
```

`RuntimeService`, the compiler, validator, simulator, reducer, and event store
remain the authoritative engine. The work adds boundaries around them rather
than moving game policy into the browser.

### Model Boundary

`OpenCodeGoSettings`, `build_model_bundle`, `SdkPlanner`, and `SdkWriter`
continue to serve the default OpenCode Go / Responses implementation. The
runtime depends on the existing `PlannerPort` and `WriterPort`, which remains
the correct isolation boundary for future providers and deterministic tests.

The production configuration remains:

```dotenv
GAL_LLM_PROVIDER=opencode_go
OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1
GAL_LLM_MODEL=deepseek-v4-flash
GAL_LLM_API=responses
```

Secrets stay only in ignored `backend/.env`. Tests and logs must report only
provider name, host, model, status, and duration, never an API key, prompt, or
raw provider error containing credentials.

### Strict Planner Contract

Replace the dynamic map in `ActionResolution` with a strict-schema-safe,
explicit list:

```python
class LearnedFactPlan(RuntimeModel):
    character_id: str
    fact_ids: tuple[str, ...]

class ActionResolution(RuntimeModel):
    # Existing fields omitted.
    learned_facts: tuple[LearnedFactPlan, ...] = ()
```

Validation rejects duplicate character IDs and duplicate fact IDs within each
entry. The simulator expands the entries into `CharacterLearnedFact` events.
This preserves the domain behavior while allowing `PlannerOutput` to use strict
structured output. Do not switch `AgentOutputSchema` to non-strict mode as the
production solution: it removes a key constraint at the model boundary.

`WriterOutput` is already strict-schema compatible. Add a regression test that
constructs strict `AgentOutputSchema` instances for both Planner and Writer
outputs without making a model request.

### Generation Failure Policy

The player-facing runtime does not commit a generic fallback scene or generic
fallback resolution after a malformed or rejected model proposal. It performs
the existing one structured-output repair attempt. If the result still fails a
model contract or deterministic validation:

- no event is appended;
- the revision remains unchanged;
- the API returns a redacted retryable generation error.

A fallback *ending in a script pack* remains valid game design: it determines
which ending is eligible at the scene limit. Its prose is still generated by
the real Writer and is not silently replaced with canned player-facing prose.

Provider failures, timeouts, and malformed responses must be represented by
typed error codes. The browser keeps the last confirmed scene visible and
offers retry. It never invents a local next scene.

## Command Reliability and Persistence

Every state-changing player operation uses a client-generated command ID:

- `advance` acknowledges a non-decision scene and generates the next scene.
- `choose` resolves one offered choice.

The browser disables controls while a command is pending but reliability does
not rely on that UI behavior. It reuses the same command ID after a lost
response or explicit retry.

Add a command receipt table, conceptually:

```text
story_command_receipts
  session_id
  command_id
  command_kind
  request_fingerprint
  status                 -- in_progress | completed
  lease_expires_at
  result_json            -- completed result only
  result_revision
  created_at
  updated_at
  PRIMARY KEY(session_id, command_id)
```

The request fingerprint covers the operation, expected revision, and choice ID
where applicable. Reusing a command ID with a different fingerprint returns a
conflict.

Command execution has these rules:

1. Claim the receipt in a short SQLite transaction. A completed matching
   receipt returns its stored response. A live matching lease returns
   `command_in_progress`. An expired lease can be safely reclaimed.
2. Execute Planner, Writer, validation, and simulation outside the database
   transaction. No model proposal changes state by itself.
3. Atomically append events and mark the receipt complete in one SQLite
   transaction. A crash yields either both the new event state and receipt, or
   neither.
4. If generation fails before commit, leave the session revision unchanged and
   release or expire the claim so the same command can be retried.
5. A stale revision or a competing command returns a typed conflict. The
   browser reloads the authoritative projection rather than guessing the
   outcome.

Session creation is intentionally not part of this receipt mechanism in the
first delivery. A duplicate new-game click may leave an unused session, but it
cannot corrupt an existing story; the UI disables the start action immediately.

## Player Projection API

The browser receives `SessionProjection`, not `SessionState`.

```text
SessionProjection
  session_id, pack_id, revision, status, phase, scene_count
  pack: title, language
  location: id, public name
  present_characters: [{id, public name}]
  scene: id, blocks, choices, can_continue
  ending: id, title, blocks (when ended)
```

The projection may include only public pack presentation metadata. It must not
include latent fact candidate values, non-revealed facts, relationship values,
goal progress, character knowledge, beliefs, secrets, prompt context, event
internals, command receipts, or database paths.

Keep the existing V2 route family. Mutation requests accept an
`idempotency_key` and return a command result containing the latest public
projection. A choice response reports its bounded action outcome; the browser
then automatically sends a separate idempotent `advance` command to request
the next real-model scene. This keeps action resolution and scene generation
separate in the kernel while providing one continuous player interaction.

Use structured error responses with stable codes, such as:

```text
command_conflict
command_in_progress
command_id_reused
generation_unavailable
model_provider_unavailable
invalid_choice
session_not_found
```

Errors contain no model response bodies or secret-bearing provider text.

### Browser Recovery Contract

The browser stores only the latest `session_id` in local storage. On boot or
refresh it requests the projection from the API. A missing session clears that
local pointer and returns the player to the start state. Starting a new game
creates a new session and replaces the stored pointer; old event streams remain
available in SQLite.

For visual presentation, the browser deterministically maps the public
`location.id` and `present_character.id` values to low-priority pixel
placeholders. This keeps all actual story truth server-side and avoids a
runtime image-generation dependency.

## Testing and Verification

### Offline Suite

The ordinary test suite must require no key and no provider requests. Extend it
with tests for:

- strict Planner and Writer schema construction;
- `LearnedFactPlan` validation and event conversion;
- duplicate command replay returning exactly the original stored result;
- command-ID reuse with a changed request being rejected;
- active versus expired receipt leases;
- atomic event-plus-receipt commits and no state change after a model failure;
- stale revision recovery behavior;
- public projection fields and hidden-state non-leakage;
- session restore and ended-session projection behavior.

### Live Capability Test

Add a `tests/live`-scoped dotenv loader with `override=False`, so a real CI
environment variable remains authoritative and normal tests remain independent
of a local `.env`. The live test stays opt-in behind `RUN_LIVE_ZEN_TEST=1` and
uses a temporary SQLite database.

It verifies the configured Responses path with a small, bounded sequence:

```text
Planner scene proposal -> strict validation
Writer scene draft -> strict validation and commit
Planner choice resolution -> validation and commit
```

The test must not print credentials or raw provider responses. A full
`play-live` autoplay run is a manual capability check, not a default test,
because it can consume many more requests.

### Dependency Reproducibility

Remove `uv.lock` from `.gitignore`, generate it after the strict contract fix,
and commit it. CI and local development then use the same resolved SDK set.
Dependency upgrades are explicit changes accompanied by the strict-schema and
live capability checks.

## Delivery Order

1. Track the lockfile and make live-test dotenv loading match the actual
   application entrypoints.
2. Convert the Planner contract to strict-schema-safe types and add its
   regression tests.
3. Implement command receipt persistence and idempotent mutation semantics.
4. Add public pack/session projections and redacted error contracts.
5. Run offline tests, lint, the live capability test, and a bounded CLI run.
6. Build the thin React browser player over the completed backend contract:
   start, restore, A/B/C/D choice sequence, retry, ending, and new game.

## Reference Research

- [Monogatari](https://github.com/Monogatari/Monogatari) informs the browser
  visual-novel player: responsive presentation, save/load, and dialogue-first
  interaction. It is a reference, not a dependency.
- [Ink](https://github.com/inkle/ink) and
  [Yarn Spinner](https://github.com/YarnSpinnerTool/YarnSpinner) demonstrate
  the valuable separation between narrative runtime outputs and game UI.
- [Freytag Forge](https://github.com/bcorfman/freytag-forge) is the closest
  architectural peer: fact-backed state, model proposals, deterministic policy,
  replay, and evaluation. Its fail-closed posture informs this design.
- [Openovel](https://github.com/Feed-Scription/openovel) is useful later for
  long-form context and diagnostics, but its background agent architecture is
  deliberately deferred to preserve a single state authority.
- [AI4VisualNovel](https://github.com/ttsmallHot/AI4VisualNovel) is a future
  reference for offline story/asset production, not a dependency for runtime
  play.
