# Dynamic Galgame Runtime Architecture

**Date:** 2026-08-12
**Status:** Proposed for review
**Scope:** Runtime architecture and script-pack contract only
**Product baseline:** [`PROJECT_GOAL.md`](../../../PROJECT_GOAL.md)

## 1. Purpose

This specification defines the target runtime architecture for the dynamic
Galgame described by `PROJECT_GOAL.md`.

The product is a traditional Galgame in interaction and presentation, but the
post-opening story is not authored as a route tree. An author supplies the
world setting, pre-opening history, characters, starting state, and completion
requirements. An Agent continues that story at runtime, presents choices at
key moments, and produces a session-specific ending. The runtime must keep the
story coherent, protect truth and character knowledge, and force a finite
conclusion without replacing the dynamic story with fixed endings.

This document is an architecture design, not an implementation plan. It does
not define an authoring UI, asset-production pipeline, accounts, publishing,
or social features.

## 2. Design Decisions

### 2.1 Primary runtime unit: performance segment

The client must not synchronize on every generated scene. The runtime unit is
a **performance segment**:

```text
opening or player choice
  -> one or more generated scenes
  -> the next player decision or a dynamic ending
```

A segment always has exactly one terminal:

- `decision`: two to four choices are presented after the segment has been
  fully played;
- `ending`: a dynamic final scene is presented and the session is closed.

Internal scenes may be used for pacing and state changes, but `continue` is not
an externally visible client state. The browser never has to issue another
network request merely because an internal scene ended.

### 2.2 Recommended generation strategy: transactional segment plus buffer

Three approaches were considered:

1. **Single streaming Agent call.** It produces text quickly, but the same
   model output decides semantics, state changes, and prose. It bypasses the
   existing Planner/Validator/Simulator boundary and cannot protect the
   project's central truth guarantees.
2. **One validated scene per request.** It preserves state safety, but a slow
   next-scene generation can interrupt a Galgame performance between choices.
3. **Transactional performance segment with a playback buffer.** The runtime
   plans and validates the entire segment, streams its draft into a server and
   browser buffer, and commits it atomically before playback is unlocked.

The third approach is the baseline. The only intentional wait is after the
player chooses an option (or when a new game starts). Once `segment_ready` is
received, the browser owns a complete playable segment and must not wait for
the model between dialogue blocks. Provisional blocks can arrive over SSE, but
they are not displayed as authoritative story until the segment is committed.

Speculative pre-generation of all option branches is an optional optimization;
correctness and the no-mid-performance-wait guarantee must not depend on it.

### 2.3 One authoritative turn command

The choice resolution and the following segment belong to one logical turn.
The target API exposes one idempotent turn command:

```text
POST /api/v2/sessions/{session_id}/turns
{
  "expected_revision": 12,
  "idempotency_key": "cmd-...",
  "choice_id": "choice-..."  // null only for opening
}
```

The command resolves the selected action, generates the next performance
segment, validates it, and atomically commits all resulting events. A provider
failure therefore cannot leave a committed player action without a next
segment. Existing separate choice/advance routes are migration wrappers only;
new browser code must use the turn command.

### 2.4 Dynamic endings and authored completion requirements

The script pack contains no ending list, ending IDs, ending titles, or ending
eligibility branches. It contains one or more author-written
`completion_requirements`.

The Agent proposes when and how the story ends and writes the final scene. A
separate Completion Judge evaluates the final simulated state and event trace
against the author's requirements. Ending and completion are separate facts:

```text
story reached a valid conclusion != completion requirements passed
```

A session may end without passing. It still receives a complete dynamic ending
and is marked `not_cleared`. The Agent may describe the meaning or tone of the
ending, but it cannot invent, remove, or reinterpret completion requirements.

### 2.5 Agent proposes; deterministic kernel commits

The Agent never directly mutates session state. All changes follow this path:

```text
Agent proposal
  -> typed contract validation
  -> deterministic policy validation
  -> pure simulation on a copied state
  -> atomic append with expected revision
  -> reducer projection
```

Narrative prose is not a fact source. The event log and the script pack are the
only authorities for truth, knowledge, relationships, goals, threads, and
completion evidence.

## 3. Product and Runtime Invariants

The following are hard requirements, not prompt suggestions:

1. The player sends only a currently presented choice ID. Free text and rewind
   are out of scope.
2. The protagonist is defined by the pack. A player choice expresses intent;
   the Agent renders the protagonist's concrete action and words.
3. The author may define only pre-opening history and persistent world rules.
   No pack field may define a post-opening plot beat, route, or future event.
4. Every visible choice has two to four unique options with distinct semantic
   intents. A choice cannot be cosmetic text variation only.
5. Every run reaches a conclusion no later than `max_scenes`, unless the model
   provider is unavailable. Provider failure leaves the run retryable rather
   than silently inventing a static ending.
6. A normal conclusion cannot occur before `min_scenes`, except for an explicit
   hard-terminal state validated by the policy layer.
7. The resolution window cannot introduce a new major thread or a new
   ungrounded premise.
8. A character may speak only from its initial knowledge plus facts learned by
   authoritative events. Belief and suspicion are not truth.
9. A latent fact may become committed only through an allowed candidate and
   evidence rule. It may not be selected merely because it makes the ending
   convenient.
10. A failed generation commits no story event. A retry uses the same
    `expected_revision` and can safely produce a different proposal.
11. A server `generation_done` event does not unlock the UI. The client waits
    for `segment_ready`, then waits for its local playback queue to drain before
    showing choices or an ending.
12. A committed segment is replayable after refresh or an SSE disconnect.

## 4. Script-Pack Contract

The next script-pack schema is conceptually `2.0`. A compatibility compiler may
temporarily read the existing schema, but the target contract must remove the
fixed `endings` field.

### 4.1 Pack sections

```text
identity
experience
world_setting
story_history
opening_state
protagonist
characters
facts
goals
completion_requirements
interaction_rules
assets
```

`goals` describe desires and pressures that already exist at the opening. They
are not a hidden route outline. Existing goal success/failure expressions may
guide runtime state and Agent context, but they cannot end a session or count
as completion unless an author explicitly references their evidence from a
`completion_requirement`. `completion_requirements` describe what the author
considers a successful completion; they do not describe how the Agent must
reach it.

### 4.2 World setting

`world_setting` is the permanent creative boundary for a run. It contains the
genre, premise, locations, factions, immutable rules, forbidden content, and
rules for introducing or interpreting facts.

Examples of valid rules:

- the world has no supernatural powers;
- death is irreversible;
- confirmed evidence cannot later be denied;
- a character cannot act on a fact it has not witnessed or learned.

World setting is not a future-event script. The Agent may invent a future
incident, but it must remain inside these rules and be grounded in the current
state.

### 4.3 Story history and opening state

`story_history` contains only canonical events before the game starts. It may
include facts that different characters remember differently, provided the
pack identifies world truth, character knowledge, and beliefs separately.

`opening_state` selects the initial location, time label, present characters,
visible facts, and the starting pressure. The first runtime segment begins from
this state; there is no authored scene after it.

### 4.4 Completion requirements

Each pack must contain at least one requirement:

```yaml
completion_requirements:
  - id: core_truth_understood
    description: "玩家必须理解导致事件发生的核心原因。"
    evidence_hints:
      fact_ids: [core_cause]
      goal_ids: [protagonist_understand_case]
  - id: protagonist_choice
    description: "玩家必须对继续调查还是保护相关人物作出不可逆选择。"
```

The `description` is the author's semantic requirement. Optional evidence hints
help the Director and Judge find authoritative evidence but cannot by
themselves mark a requirement complete. The Completion Judge returns one
assessment per requirement with `satisfied`, cited event IDs, and a short
rationale. The kernel verifies that cited events exist and are compatible with
the final state before aggregating `cleared = all(required requirements)`. The
Judge cannot add a requirement or alter its wording.

The pack must not contain `ending.type`, `ending.priority`, `ending.title`, or
ending eligibility conditions. A generated ending ID is scoped to the session.

## 5. Target Components

```text
Pack Compiler / Registry
          |
          v
Turn Orchestrator  <---->  Story Event Store
   |       |    \
   |       |     +--> Public Projection
   |       |
   |       +--> Deterministic Pacing / Ending Policy
   |
   +--> Action Resolver Agent
   +--> Segment Director Agent
   +--> Segment Writer Agent (provisional stream)
   +--> Knowledge / Canon Guard
   +--> Completion Judge (ending only)
   +--> Validator + Simulator + Reducer
          |
          v
     SSE Segment Protocol
          |
          v
     Browser Producer/Consumer Player
```

### 5.1 Turn Orchestrator

The orchestrator is the only service allowed to run a player turn. It owns the
command receipt, revision check, model-call sequence, retry budget, segment
buffer, and atomic commit.

It must not stream a block as committed state before the complete segment has
passed validation and simulation. It may forward provisional blocks to the
client buffer, but the client cannot unlock playback until the commit marker.

### 5.2 Action Resolver Agent

For a non-opening turn, the resolver converts the presented choice into a typed
`ActionResolution` containing outcome, bounded relationship changes, bounded
goal changes, evidence/revelation requests, learned facts, and dynamic thread
effects.

It does not write dialogue or choose a latent value outside the pack's
candidates. The existing `validate_action_resolution` and
`simulate_resolution` responsibilities remain in the deterministic kernel.

### 5.3 Segment Director Agent

The Director receives the post-choice candidate state, the full world truth,
the event trace digest, the character knowledge map, completion requirements,
open threads, and a deterministic pacing envelope.

It returns a `SegmentPlan`, not prose. A plan may contain one or more internal
scenes and exactly one terminal decision or ending proposal. It may propose
runtime-created threads, fact commits, evidence, beliefs, relationship and
goal effects, but every proposal is checked by the kernel.

The Director may decide to end the story when the narrative has a defensible
conclusion. It does not choose an authored ending ID because none exists.

### 5.4 Segment Writer Agent

The Writer renders only an approved `SegmentPlan` as Galgame blocks and choice
labels. It cannot add a fact, effect, character, location, choice ID, thread,
or ending obligation. It returns:

- ordered narration and dialogue blocks;
- public presentation metadata references;
- labels and previews for the exact planned choices;
- a dynamic ending title and final blocks only when the plan is an ending.

The Writer context is scoped per speaker. It receives the world rules, public
facts, approved narration facts, and each present character's own knowledge,
beliefs, voice, and boundaries. It must not receive an unfiltered list of every
character's secrets as a shared prose context.

### 5.5 Canon and knowledge guard

The guard has two layers:

1. Deterministic checks for IDs, speaker presence, known fact IDs, visibility,
   evidence counts, world-rule references, choice identity, scene limits, and
   plan/draft equality.
2. A bounded semantic critic for suspected knowledge leaks, contradiction with
   immutable rules, unsupported certainty, and dialogue that attributes a fact
   to the wrong speaker.

Critic output is a typed list of violations with block indices and authorized
fact IDs. A violation rejects the segment; it does not mutate state.

### 5.6 Completion Judge

The Judge is called only for a proposed ending, against the simulated final
state and authoritative event trace. It receives the author's requirements,
not an Agent-generated success label. It returns evidence-backed assessments.
The kernel computes the final `cleared` boolean and persists both the
assessment and its cited evidence.

## 6. Segment Generation Data Flow

```text
Turn command (opening or current choice)
  -> load pack and replay session
  -> validate current choice / resolve action
  -> simulate choice effects on candidate state
  -> derive pacing envelope and ending policy
  -> Director proposes SegmentPlan
  -> deterministic plan validation
  -> Writer produces provisional blocks and choice labels
  -> structural validation + canon/knowledge guard
  -> simulate all segment events on candidate state
  -> if ending: Completion Judge evaluates simulated final state
  -> atomic append: action, effects, scenes, decision/ending,
     completion assessment, receipt
  -> emit segment_ready
  -> browser drains the buffered performance
```

The model calls happen outside the final database transaction. The command
receipt reserves the idempotency key, and the final append uses the original
expected revision. A competing command or stale revision discards the
candidate and returns a typed conflict; no partial proposal is committed.

## 7. Pacing and Convergence Policy

The pack supplies `min_scenes`, `max_scenes`, and
`reserved_resolution_scenes`. The kernel derives a pacing envelope from these
values and the current state.

### 7.1 Pacing phases

The target phases are:

```text
opening -> exploration -> escalation -> crisis -> resolution
```

Phase changes are deterministic from scene budget and may be accelerated by an
irreversible hard-terminal state. The phase is an input to the Director, not a
future plot instruction.

### 7.2 Thread budget

The Agent may create a narrative thread during a segment, but the kernel
assigns its session-local ID and records its introduction event. Each thread
has participants, related facts, urgency, and a terminal disposition.

- Before the convergence window, new threads are allowed within the pack's
  open-thread budget.
- At `max_scenes - reserved_resolution_scenes`, no new major thread may open.
- In resolution, every active thread must be resolved, abandoned, or explicitly
  left with a stated consequence. A conclusion need not answer every mystery,
  but it must make the remaining uncertainty intentional.
- A segment that adds only filler without advancing a thread, goal, fact,
  relationship, or pressure is rejected after the configured quiet-scene
  allowance.

### 7.3 Ending authorization

The Ending Policy authorizes a normal dynamic ending only after `min_scenes` and
when the proposed closure has a valid disposition for the active threads. The
policy enters forced resolution when the remaining budget reaches the reserved
resolution window. At `max_scenes`, an ending is mandatory.

Forced resolution does not reveal uncommitted latent facts or invent a hidden
mastermind. It renders the final state, including unresolved or abandoned
threads, as a coherent consequence of the run.

## 8. State and Event Model

The existing immutable `SessionState`, reducer, revisioned EventStore, fact
records, character knowledge, beliefs, relationships, goals, and narrative
threads remain the foundation. The target model separates presentation from
decision and removes the fixed-ending assumption.

### 8.1 Target event groups

```text
PlayerActionSelected
ActionResolved
RelationshipChanged / GoalAdvanced
FactCommitted / FactEvidenced / FactRevealed
CharacterLearnedFact / BeliefChanged
ThreadOpened / ThreadAdvanced / ThreadClosed
PhaseAdvanced
SceneCommitted (one per internal scene)
DecisionPresented (only at segment terminal)
EndingGenerated
CompletionEvaluated
SessionEnded
```

`SceneCommitted` stores replayable blocks and scene metadata. A decision is
presented once per segment and contains only the exact choices rendered by the
Writer. `EndingGenerated` stores a session-local ID, title, blocks, terminal
state summary, and dynamic tone; it does not refer to a pack ending.

### 8.2 Atomicity

All events resulting from one turn are appended in one expected-revision
commit. The browser may have received provisional blocks, but those blocks are
discarded on any failed validation. A committed segment can be replayed from
the receipt or the event log after a lost response.

### 8.3 Public projection

The browser receives only public metadata and committed segment content:

```text
session_id, pack_id, revision, status, phase, scene_count
location: public id/name
present characters: public id/name
segment: blocks, terminal, choices
ending: title, blocks, cleared status, requirement summaries
```

The projection never exposes latent candidates, hidden facts, private
knowledge, beliefs, relationship values, goal progress, prompt context,
internal events, receipts, or provider details.

## 9. Async Segment Protocol

The target turn endpoint returns `text/event-stream`.

```text
event: segment_started
data: {"segment_id":"seg-...","expected_revision":12}

event: block
data: {"segment_id":"seg-...","index":0,"kind":"dialogue",...}

event: segment_ready
data: {
  "segment_id":"seg-...",
  "revision":18,
  "terminal":"decision",
  "choices":[...]
}
```

For an ending, `segment_ready` contains `terminal: "ending"`, the session-local
ending metadata, and the completion assessment. `block` events are provisional
until `segment_ready`; the client must retain them in a hidden buffer and must
not show them as committed story before that marker.

The stream may also send:

- `heartbeat` to keep a long generation alive;
- `error` with a stable redacted error code;
- `retry_after` when the command lease is still active.

The browser playback state machine is:

```text
idle
  -> generating_after_choice
  -> buffering_segment
  -> playing
  -> waiting_choice | playing_ending
  -> ended
```

`segment_ready` moves the player from buffering to playing. Transport EOF,
`generation_done`, and receipt completion never directly change the visual
state. The player changes to `waiting_choice` or `ended` only after all blocks
are read. A refresh can replay the committed segment from the public
projection; it does not ask the model to continue the story again.

## 10. Error, Retry, and Recovery Rules

- The command receipt has `in_progress`, `committed`, and retryable failure
  states with a lease timeout.
- The same idempotency key and request fingerprint replay the committed segment
  or retry a failed generation. Reusing a key with a different choice or
  revision is rejected.
- Contract repair and semantic guard retries are bounded. A failed repair
  releases the candidate without changing state.
- A network disconnect after commit is recovered by receipt replay or a session
  projection containing the committed segment.
- A disconnect before `segment_ready` leaves the session at its previous
  revision. The provisional client buffer is discarded.
- A stale revision returns `revision_conflict`; the browser reloads the public
  projection and never guesses whether a choice was applied.
- A provider outage is not converted into a fake authored ending. The session
  remains retryable, including when it is in forced resolution.
- No error response includes raw prompts, hidden facts, model output, API keys,
  or database paths.

## 11. Migration Boundary from the Current Repository

### Reuse

- SQLite append-only `StoryEventStore`, revisions, and command receipts.
- Immutable state models and reducer validation.
- Fact truth/visibility, character knowledge, beliefs, relationships, goals,
  and narrative thread primitives.
- Condition compiler for optional deterministic evidence hints.
- Planner, Writer, Validator, and Simulator ports as the base interfaces.
- SSE transport and the frontend typewriter/queue concepts.
- Asset fields and event references as empty future-compatible interfaces.

### Replace or reshape

- Replace `ScriptPackSource.endings` and the three-normal-plus-fallback compiler
  rule with `completion_requirements`.
- Replace `select_ending()` with `EndingPolicy`, `EndingCoordinator`, and
  `CompletionJudge`.
- Replace one-scene `advance_streamed()` with the transactional
  `TurnOrchestrator` and performance-segment protocol.
- Do not let `stream_writer.py` invent facts, choices, terminal states, or
  effects. It becomes a Writer adapter that consumes an approved plan.
- Split the current overloaded scene/decision/ending state transitions so a
  segment can contain multiple internal scenes and exactly one terminal.
- Replace the frontend transition on SSE `done` with the
  `segment_ready -> local playback drained -> terminal UI` state machine.
- Migrate the example pack from fixed endings to world setting, history, and
  completion requirements. Its future story must not be moved into a new beat
  list.

No compatibility layer may preserve fixed ending semantics under a new name.
During migration, the old pack can remain as a fixture, but the target runtime
must reject fixed ending definitions in production packs.

## 12. Verification and Evaluation

### 12.1 Offline contract tests

The test suite must cover:

- pack compilation with world setting, history, opening state, and at least one
  completion requirement;
- rejection of fixed ending fields and future beat sections;
- strict JSON schemas with no open-ended dynamic mappings;
- plan validation for locations, speakers, choices, fact evidence, thread
  budgets, and scene limits;
- writer output that cannot change plan IDs or semantic effects;
- no state change when any segment validation fails;
- atomic commit and exact idempotent replay;
- forced resolution at `max_scenes` and dynamic session-local ending IDs;
- ending versus cleared-status independence;
- hidden-state redaction in public projections.

### 12.2 Runtime property tests

Run deterministic fake-Agent sessions under several player policies:

- always choose first option;
- always choose last option;
- alternate choices;
- choose options that increase and decrease trust;
- deliberately trigger latent-fact ambiguity.

Every successful provider run must satisfy:

- `scene_count <= max_scenes`;
- exactly one ending and one completion assessment;
- no duplicate or unavailable choice IDs;
- no character speaks an unauthorized fact;
- no fixed pack ending ID is selected;
- no open-ended segment after forced resolution;
- event replay equals the committed projection.

### 12.3 Adversarial knowledge evaluation

Add probes that ask characters to reveal another character's secret, state an
unwitnessed latent value, contradict committed evidence, or use a narrator
summary as proof. The guard must reject or repair the segment, and the final
event log must contain zero unauthorized knowledge events.

### 12.4 Browser playback tests

The frontend tests must prove that:

- blocks can arrive before playback starts and are played in order;
- the player does not transition on transport `done` alone;
- a choice is not displayed until the local queue drains;
- ending metadata waits for final-block playback;
- a connection failure before `segment_ready` leaves the old revision intact;
- replay after refresh does not issue a duplicate turn;
- there is no client request between internal scenes.

### 12.5 Product metrics

The runtime should record privacy-safe metrics for later evaluation:

- segment generation latency and time to `segment_ready`;
- validation/repair/rejection rates;
- buffer underrun count, which must be zero for the transactional baseline;
- sessions reaching an ending before and at the scene cap;
- completion pass rate by pack and player policy;
- choice diversity and repeated-label rate;
- fact contradiction and knowledge-leak probe rate;
- proportion of endings forced by the scene cap.

## 13. Acceptance Criteria

The architecture is considered aligned with the product goal when a test pack
with no post-opening plot beats and no fixed endings can run multiple seeded
sessions where:

1. the player sees continuous Galgame-style performance between decisions;
2. the only normal wait is after opening or a player choice;
3. each choice is server-presented and has a real state effect;
4. the Agent produces different, causally coherent future stories;
5. characters never gain unauthorized facts;
6. every run ends within the configured scene budget;
7. each ending is generated for that session rather than selected from a pack
   ending list;
8. the author-written completion requirements are evaluated separately from the
   ending narrative;
9. a run may end without passing, but it never ends without a valid dynamic
   conclusion and completion assessment;
10. refresh, duplicate commands, provider retries, and SSE disconnects do not
    corrupt or fork the authoritative story state.
