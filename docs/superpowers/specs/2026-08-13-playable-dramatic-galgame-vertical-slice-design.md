# Playable Dramatic Galgame Vertical Slice Design

- Date: 2026-08-13
- Status: approved design
- Product target: a complete, replayable Web vertical slice of `cafe_mystery`
- Primary authority: `PROJECT_GOAL.md`

## 1. Outcome

Build `cafe_mystery` into a 30-45 minute dynamic Galgame that a player can finish in one browser session and immediately want to replay from a different stance.

The first release validates one product claim:

> The player can form a meaningful relationship with a character, make difficult choices, see those choices produce specific remembered consequences, and reach a causally complete dynamic ending without waiting through repeated generation pauses.

The initial experience uses:

- 6-10 meaningful choices;
- roughly 8-14 performed scenes;
- Alice as the primary emotional axis;
- truth and evidence as the structural mystery;
- dynamic consequences and endings as the replay value;
- Web as the only supported product surface.

This milestone does not build the multi-pack platform. It proves the complete play loop with one pack while keeping the runtime pack-driven rather than hard-coding cafe-specific behavior.

## 2. Product Decisions

The following decisions are fixed for this vertical slice:

1. Relationship impact is the primary player experience.
2. Investigation supplies structure, but the game is not primarily a deduction puzzle.
3. Dynamic causality supplies replay surprise.
4. The timing of generation is an implementation detail. Text does not need to be generated after the click to count as dynamic.
5. The runtime may pre-generate state-bound candidates while the player reads.
6. A rolling short-term dramatic promise system is the main mechanism for causal continuity.
7. Internal state may be numeric and exact, but player-facing feedback is performed through character behavior and prose.
8. Alice is the primary emotional focus. Bob and Mina apply conflicting pressure rather than receiving equal screen time by quota.
9. Model and provider may change. Quality and contract reliability take priority over inference cost for the first playable release.
10. The minimum Web presentation includes fixed backgrounds and basic character art, but advanced presentation cannot block the gameplay loop.
11. The first deliverable is only `cafe_mystery` and the reusable engine capabilities it requires.

## 3. Current-State Findings

The existing repository has valuable reliability foundations:

- append-only event storage with revisions and idempotency;
- deterministic validation, simulation, reduction, and atomic commit;
- separation of world truth, character knowledge, beliefs, and dialogue;
- typed model contracts;
- a real SSE browser client;
- a V2 pack format that avoids author-written future plot trees;
- latent questions that can delay committing a truth until evidence makes it irreversible.

It does not yet prove a playable product:

- the runtime state is stronger at preserving consistency than producing dramatic consequences;
- choices mainly describe action verbs such as `ask`, `observe`, and `challenge`, not the values, risks, sacrifices, or obligations expressed by the action;
- relationship deltas can be committed without a durable semantic relationship event explaining why they changed;
- the `irreversible_choice` completion requirement in `cafe_mystery` has no evidence hints, and the deterministic completion judge therefore cannot mark it satisfied;
- existing saved openings are coherent, but choices mostly select who to question rather than force a difficult stance;
- the documented `play-live` command invokes the obsolete fixed-ending runtime for a V2 pack and raises `TypeError`;
- a direct test of the current `/turns` production path produced no playable block after more than two minutes before being interrupted;
- the server advertises SSE, but the client receives useful prose only after the full segment has been generated, validated, and committed;
- multiple runtime paths and old documentation make it possible to test behavior that is not the product path;
- current automated live tests accept intermittent second-turn failure as a skip, which is unsuitable for a playable release gate.

The design therefore preserves the trustworthy state kernel and replaces the gameplay semantics around it.

## 4. Chosen Approach: Dramatic State Engine

The runtime becomes a dramatic state engine rather than a general segment continuation engine.

The engine remains the authority. The model proposes what happens and performs it in prose. It cannot commit facts, relationships, promises, obligations, or completion evidence directly.

Each turn follows this loop:

```text
character desire or pressure becomes immediate
  -> player faces a conflict between values
  -> engine records the player's action and stance
  -> next performance immediately acknowledges the choice
  -> a promised short-term consequence changes a later situation
  -> pressure escalates, turns, or resolves
  -> dynamic ending pays off the accumulated relationships and costs
```

The runtime does not prescribe future events. It requires already established causes to receive a timely response.

### Rejected Approach: Patch the Current Segment Engine

Changing only prompts, models, caching, and scene counts would produce a faster and more coherent version of the current system, but it would not provide a strong engine-level representation of why a choice matters. It risks producing a stable AI novel instead of a game.

### Rejected Approach: Fully Autonomous Character Agents

Independent agents for Alice, Bob, Mina, and a director increase latency and coordination failure without proving better choices or consequences. Character-level model deliberation may be introduced later for a measured scene-quality benefit, but it is not the core architecture.

## 5. Authority and State Model

The authority is divided into three layers.

### 5.1 Fact Layer

The fact layer extends the existing world model.

It stores:

- immutable world rules;
- fixed and latent facts;
- character knowledge and beliefs;
- clues with source, witnesses, reliability, and supported or contradicted interpretations;
- committed latent answers and the evidence that made them irreversible.

Existing principles remain:

- narration is not an authority;
- model output cannot mutate state;
- a character cannot speak from information they have not learned;
- committed evidence cannot be denied later;
- summaries are context aids, not fact sources.

### 5.2 Character Layer

Numeric relationship axes remain useful internal aggregates, but they are no longer the primary explanation of a relationship.

The engine also stores semantic character state:

```text
current desire
current fear
active emotional condition
current judgment of the protagonist
boundary being tested
relationship events with evidence
unresolved interpersonal debt
irreversible relationship turning points
```

A relationship delta must cite a committed event. Examples include:

- the protagonist publicly trusted Alice;
- the protagonist exposed Alice's lie;
- the protagonist protected Mina's secret at personal cost;
- the protagonist changed sides when evidence became inconvenient;
- Bob accepted risk because the protagonist kept a promise.

Future generation receives the semantic events that matter, not only `trust=63`.

### 5.3 Dramatic Layer

The dramatic layer is new engine authority.

#### Dramatic Question

The most important unresolved human question in the current sequence, such as:

> Will the protagonist still trust Alice when the evidence turns against her?

Only one question is primary at a time. Secondary questions may exist but cannot compete for every scene.

#### Promise

An expectation established for the player that requires escalation, transformation, or payoff. A promise records:

- the originating event;
- what the story has made the player expect;
- involved characters and facts;
- a soft and hard payoff deadline measured in decisions;
- allowed outcomes: escalate, transform, fulfill, or deliberately break with consequence;
- current status and payoff evidence.

#### Obligation

A responsibility created by a player or character action. Examples include keeping a secret, returning help, explaining a lie, or accepting responsibility for another person's exposure.

#### Stance

A durable interpretation of what the protagonist has expressed through action, such as:

- trust Alice despite incomplete evidence;
- evidence outranks personal loyalty;
- protection justifies concealment;
- no one has the right to decide for another person.

A stance is not a personality quiz score. It cites choices and may be reinforced, qualified, contradicted, or defended under pressure.

#### Scheduled Consequence

A bounded requirement that a previous action change the situation within the next one to three decisions. It specifies the cause and required type of effect, not a fixed event.

#### Turning Point

An irreversible change in a relationship, truth, or responsibility. Turning points are primary ending evidence.

#### Arc Pressure

A deterministic pacing policy with three product phases:

1. `approach`: attraction, curiosity, initial judgments;
2. `fracture`: previously compatible values and relationships conflict;
3. `accountability`: the protagonist must carry the cost of earlier choices.

The phase limits what the model may introduce, but does not prescribe a future scene.

## 6. Turn Contract

The model is not asked to "continue the story." It receives a bounded dramatic assignment.

Every normal turn must:

1. acknowledge the previous choice within the opening performance blocks;
2. advance one active character desire;
3. escalate, transform, or pay off at least one existing promise when one is due;
4. avoid opening more than one small new question;
5. materially change at least one authoritative gameplay state;
6. end at a meaningful decision with a real value conflict, unless the story is ending;
7. explain all proposed state changes through evidence in the performed scene.

The structured response contains:

```text
scene plan
performance blocks
state-change proposals
promise and obligation operations
choice contracts or dynamic ending
whitelisted presentation cues
```

For auditing, the proposal identifies:

- which previous choice it acknowledged;
- which desire it advanced;
- which promise it changed;
- what each central character gained or lost;
- why the resulting choices conflict;
- which visible scene evidence supports every proposed mutation.

The local kernel applies structural validation, knowledge validation, dramatic validation, clone-first simulation, and atomic commit.

### Empty-Turn Rejection

A segment is rejected as dramatic no-op when all of the following remain unchanged:

- relevant character desire or judgment;
- relationship events;
- promises or obligations;
- clue interpretation or truth progress;
- risk or cost;
- primary dramatic question;
- arc pressure.

Prose volume alone never makes a turn valid.

## 7. Choice Contract

Choices remain the player's only story input.

The low-level action vocabulary may remain for validation, but each choice also records:

```text
performed action
target
expressed stance
protected value or person
accepted risk or sacrifice
potential obligation
conflict axis shared with sibling options
```

Default decision size is three choices. Two are acceptable when the conflict is genuinely binary. Four are exceptional.

Choice rules:

- sibling choices must differ in value, cost, or relationship meaning;
- labels say what the protagonist will do and any reasonably foreseeable risk;
- labels do not reveal deterministic hidden consequences;
- the set must not contain an option that is simultaneously kind, clever, safe, and cost-free;
- silence or withdrawal is allowed, but costs opportunity, trust, information, or responsibility;
- synonymous action variants are invalid;
- choices must respect the authored protagonist's identity and boundaries.

Example:

```text
Publicly trust Alice and demand that Bob return the notebook.
Refuse to take sides and expose the contradiction in Alice's account.
Protect Alice in public, but privately require a complete explanation.
```

These express public trust, evidence-first distance, and conditional protection. They do not prescribe the outcome.

### Consequence Timing

Every committed choice has three potential consequence layers:

1. Immediate: the next performance visibly acknowledges it.
2. Short-term: a character acts on it within one to three decisions.
3. Ending: important promises, harm, sacrifice, and unpaid obligations are resolved in the dynamic ending.

The scheduler enforces the short-term deadline. Missing a hard deadline is a generation failure, not permission to forget the choice.

## 8. `cafe_mystery` Pack Redesign

The pack remains an authored starting state rather than a future plot.

### 8.1 Experience Bounds

```text
expected duration: 30-45 minutes
decisions: 6-10
performed scenes: about 8-14
locations: cafe, back alley, old library
primary emotional axis: Alice
structural mystery: the notebook and the Veiled Circle
```

The internal causal slice is shorter and is defined by outcome invariants rather than a beat sequence:

- the player establishes a meaningful stance within the first two decisions;
- a later state makes that stance conflict with another value or relationship;
- within one to three further decisions, the player reinforces, revises, or abandons the stance and the engine records a real cost or obligation outcome;
- the dynamic short ending pays off that stance and its consequences.

The runtime remains free to decide which location, evidence, character action, and scene arrangement create those conditions. These invariants are acceptance criteria for the engine and test harness. They are not pack fields, model-authored future instructions, or mandatory events. This 10-15 minute slice proves the engine before content expands to the final bounds.

### 8.2 Character Conflict

- Alice is emotionally accessible, impulsive, and afraid that truth will cost her support.
- Bob values verifiable evidence and safety, but turns care into control.
- Mina protects people and the cafe, but assumes the right to decide what others should know.

The external mystery creates pressure on these incompatibilities. It is not a substitute for them.

### 8.3 Latent Truth

The notebook holder remains a latent question. Its answer changes the meaning of the interpersonal conflict:

- Bob may have taken it to prevent a previous disaster from recurring.
- Mina may have hidden it while judging who can carry the risk.
- A third party may already hold it, meaning none of the three central characters began as the sole culprit and their existing assumptions are incomplete.

These are authored possible truths present before play, not authored future routes. The runtime commits one only when irreversible evidence exists.

### 8.4 Initial Conflict Axes

The pack may declare value incompatibilities that already exist in the starting character definitions:

```yaml
conflict_axes:
  - id: trust_vs_evidence
    values: [trust, evidence]
    source_character_ids: [alice, bob]
    initial_incompatibility: >
      Alice needs personal trust before she can disclose everything, while
      Bob refuses trust that is not supported by verifiable evidence.
```

An axis describes only a conflict that is true before play. It cannot contain activation conditions, deadlines, escalation questions, future actions, expected scenes, or payoff instructions. The runtime derives its current dramatic question and consequences from committed play state. Pack compilation rejects future-looking conflict fields.

### 8.5 Machine-Verifiable Completion

Every requirement must declare deterministic evidence. Requirements with no evidence rule are rejected at pack compilation.

The first pack uses the following semantic requirements:

```yaml
completion_requirements:
  - id: truth_understood
    all:
      - fact_revealed:
          fact_id: notebook_holder
      - fact_revealed:
          fact_id: notebook_disappearance_cause

  - id: meaningful_bond
    any:
      - relationship_turning_point:
          turning_point_id: alice_mutual_trust
      - relationship_turning_point:
          turning_point_id: bob_earned_respect
      - relationship_turning_point:
          turning_point_id: mina_shared_responsibility

  - id: accepted_cost
    any:
      - obligation_fulfilled:
          min_burden: 1
      - cost_incurred:
          min_severity: 1
      - stance_defended:
          min_challenges: 1
          min_cost_severity: 1
```

The compiler contract is a recursive evidence expression with non-empty `all`, non-empty `any`, and exactly one typed leaf per leaf node. Unknown predicates, unknown IDs, empty groups, mixed leaf/group objects, and requirements without evidence fail pack compilation.

The first release supports these leaves with exact matching semantics:

- `fact_revealed.fact_id` matches a committed `FactRevealed` event for that pack fact. Its assessment cites the `FactRevealed`, the fact's `FactCommitted`, and the fact's evidence event IDs.
- `relationship_turning_point.turning_point_id` matches a committed `RelationshipTurningPointReached` with that pack-declared ID. A turning point definition contains `character_id`, non-empty `all_of_event_tags`, and `min_distinct_source_choices >= 1`. The kernel emits the event exactly once after the required immutable `RelationshipEventRecorded` tags exist for the character across the required number of selected choices. The assessment cites the turning-point event and its constituent relationship events.
- `obligation_fulfilled.min_burden` matches an `ObligationResolved(outcome="fulfilled")` whose corresponding `ObligationCreated.burden` is at least the operand. Obligation kind and burden come from the selected choice's validated `potential_obligation_kind` and the pack's declared obligation-kind definition; the model cannot assign burden. The assessment cites creation, fulfillment, and fulfillment-scene evidence.
- `cost_incurred.min_severity` matches a kernel-derived `CostIncurred` at or above the operand. The kernel derives cost only from a qualifying committed effect: a relationship loss of at least `5 * severity` supported by a semantic relationship event, or a newly accepted pack-declared obligation whose burden is at least the severity. It cites both the derived cost and qualifying effect events.
- `stance_defended.min_challenges` and `min_cost_severity` match a `StanceExpressed(relation="reinforced")` when the same stance was previously established, at least the requested number of `StanceChallenged` events occurred after establishment, and the reinforcing choice caused a qualifying `CostIncurred`. It cites the stance history, challenges, choice, cost, and effect evidence.

The pack declares the finite relationship event tags, turning-point definitions, and obligation kinds used by its requirements. Each obligation kind declares an integer burden from 1 to 3 and allowed resolution outcomes; neither is a model-supplied free value. The model may propose only declared relationship tags and obligation kinds. `RelationshipEventRecorded` stores `character_id`, `tag`, `source_choice_event_id`, and `scene_event_id`. Obligations store a stable `obligation_id`, declared `kind`, kernel-copied burden, source choice, and resolution scene. Stance events store a canonical axis/value key and their source choice. These semantic events, not prose or evaluator opinion, determine completion.

Understanding, relationship, and accepted cost must all have committed evidence. `notebook_disappearance_cause` is a second latent fact whose value explains why the notebook changed hands; requiring it avoids treating knowledge of the holder alone as understanding the mystery.

Passing and ending remain separate. A failed run receives a complete causal ending.

## 9. Generation and Pre-Generation

### 9.0 Production-Model Feasibility Gate

Before implementing rolling frontier generation, run the intended production contract against real providers. The benchmark uses 20 opening-state requests and 60 choice-state requests across at least 10 fixed seeds, with the complete structured payload and local validator enabled.

The candidate model configuration may proceed only when:

- at least 95% of requests pass directly or after the single allowed repair;
- at least 85% pass without repair;
- uncached single-candidate P95 latency is at most 15 seconds during this architecture gate;
- generating three choice candidates concurrently does not exceed provider concurrency limits or produce more than 5% throttled requests;
- no accepted result violates knowledge, choice-conflict, promise, or evidence rules.

The 15-second gate is not the product target. It establishes that rolling pre-generation can plausibly hide generation during reading. If no evaluated provider passes, change the model contract or provider before building the cache architecture.

### 9.1 Online Generation

The normal path uses one primary structured model call per candidate. It returns plan, prose, proposals, choices, and presentation cues together.

The synchronous local pipeline is:

```text
structured model response
  -> schema validation
  -> fact and knowledge validation
  -> choice and consequence validation
  -> dramatic no-op detection
  -> clone-first state simulation
  -> atomic event commit
```

An online semantic judge is not required on every accepted turn. Semantic judging is used for offline evaluation, candidate comparison, and diagnostics. One repair call is allowed after a rejected structured response.

### 9.2 Opening

Validated opening candidates are generated at pack publication, deployment, or explicit warm-up. Starting a new game selects a valid state-compatible candidate and begins playback immediately.

The opening may be dynamically generated before the session click. Dynamic describes dependence on the pack, seed, and state, not the wall-clock moment when inference ran.

### 9.3 Rolling Candidates

While the player reads, the server generates candidates for the two or three current choices.

Each candidate is bound to:

- pack hash, prompt version, model adapter version, structured contract version, validator policy version, reducer schema version, and drama engine version;
- authoritative state hash;
- choice ID and choice semantics;
- model identity and generation parameters;
- active promises and obligations;
- proposed event batch;
- validation result.

The opening-state fingerprint is canonical JSON over pack hash, session seed, opening world state, facts, character state, and drama state. It excludes `session_id`, `created_at`, revision-envelope IDs, and other storage identity, so a warmed opening can be shared only when its complete gameplay authority is identical.

After selection, the runtime verifies every version and state binding, runs the full current deterministic validation pipeline again, re-simulates the proposal against current authority, commits the choice and performance atomically, and starts rolling generation for the new frontier. A cached validation result is diagnostic metadata and never authorizes consumption by itself.

No candidate may be reused across divergent histories merely because its prose appears compatible.

### 9.4 Cache Miss and Failure

Targets:

- start click to first text: under 500 ms locally;
- pre-generated choice to first response: under 500 ms;
- uncached normal choice: P95 under 8 seconds;
- no more than one noticeable wait in a normal full run;
- primary model acceptance: at least 85% direct and at least 95% direct-or-one-repair.

The causal-slice milestone does not split a selected turn into an immediate micro-segment and a later continuation. A cache miss shows a bounded generation wait and commits the complete validated turn atomically. Only add a two-stage continuation protocol after telemetry shows that rolling pre-generation cannot meet the P95 target; that protocol requires its own design because it introduces an authoritative `continuation_pending` state. Generic filler that changes nothing is never permitted.

Provider failure preserves the previous authority. The system retries or switches configured providers within a bounded policy. Exhaustion yields a clear retry state and never fabricates story progress or loses the choice.

## 10. Unified Product Path

The new drama turn engine is the only production mutation path.

The following surfaces use the same application service:

- Web `/turns` API;
- CLI play and autoplay;
- automated player policies;
- live model evaluation;
- deterministic test adapters.

The obsolete `RuntimeService`, `/advance`, separate choice mutation, and fixed-ending selection path are removed after migration. Documentation must not advertise a compatibility path that exercises different rules.

When the drama turn service becomes the production owner, `/advance` and `/choices/{choice_id}` are disabled in the same release and their structural absence is tested. Physical deletion of now-unreachable modules may follow after the causal slice passes, but two mutation authorities never remain exposed together.

Live tests cannot convert a normal generation failure into a skip for release gating. Provider experiments may remain opt-in, but the chosen production model configuration must pass a bounded repeatable acceptance suite.

## 11. Web Vertical Slice

Web is the only supported client for this milestone.

### Required

- React/Vite browser player;
- desktop-first layout with basic mobile usability;
- narration, dialogue, and protagonist internal thought;
- click, Enter, or Space progression;
- click once to finish the current typewriter line;
- choice screen after the performance queue drains;
- dialogue history;
- refresh and resume of the current session;
- dynamic ending with pass/fail status;
- 3-5 natural-language key turning points from committed evidence;
- state-bound pre-generation with immediate playback when ready;
- fixed background and character assets sufficient to read the scene as a Galgame.

### Minimal Assets

- one base portrait for Alice, Bob, and Mina;
- one background for each of the three locations;
- one consistent UI treatment;
- optional single looping BGM track.

Placeholder geometry is not an acceptable final vertical-slice presentation, but advanced asset production cannot delay validating the play loop.

### Deferred

- native desktop and mobile apps;
- Live2D, voices, runtime image generation, and CG production;
- advanced camera and stage direction;
- elaborate save management;
- autoplay and read-text skip;
- full audio mixer and presentation settings;
- marketplace, accounts, cloud saves, and community features.

## 12. Data and Event Flow

A choice turn executes as follows:

```text
Web submits choice ID + expected revision + idempotency key
  -> service loads authoritative state and current decision
  -> service validates the offered choice and candidate binding
  -> selected choice emits semantic choice events
  -> candidate proposal is validated and simulated on a copy
  -> one atomic batch commits choice, dramatic mutations, scene, and next decision
  -> Web receives committed blocks and starts playback
  -> background workers generate the next choice frontier
```

The event model includes semantic equivalents of:

- `choice_selected`;
- `stance_expressed`;
- `relationship_event_recorded`;
- `promise_opened`, `promise_changed`, `promise_paid_off`;
- `obligation_created`, `obligation_resolved`, `obligation_broken`;
- `consequence_scheduled`, `consequence_realized`;
- `turning_point_reached`;
- fact, knowledge, belief, goal, scene, decision, completion, and ending events.

Names may be consolidated during implementation. Replay must reconstruct the complete dramatic authority without reading generated prose as state.

Model proposals use proposal-local evidence references such as `scene:current`, `choice:selected`, and `effect:relationship_1`. Before simulation, the kernel validates that every reference resolves within the proposal or to an existing committed event, preallocates UUID event IDs for the whole ordered batch, replaces local references with those IDs, and then applies the batch in this order:

1. selected choice and expressed stance;
2. action/effect and semantic relationship events;
3. fact, knowledge, promise, obligation, consequence, and turning-point changes;
4. committed performed scenes;
5. next decision or ending;
6. completion evaluation and session end when terminal.

The event store accepts preallocated envelopes only after checking contiguous sequences, unique event IDs, and session identity. This makes same-batch evidence references deterministic before the SQLite transaction while preserving atomic commit.

## 13. Error Handling

### Model Contract Failure

Reject the candidate without modifying session state. Allow one bounded repair attempt. Record prompt version, model, rejection reason, latency, and token use for evaluation.

### Dramatic No-Op

Reject and regenerate with the failed invariants included. A no-op never becomes acceptable because it is well written.

### Knowledge or Fact Violation

Reject locally before commit. The repair prompt receives only the violation category and permitted context; it does not weaken the authority.

### Promise Deadline Risk

The pacing assignment prioritizes the due promise. At a hard deadline, candidates that neither pay off nor produce an explicit consequential break are invalid.

### Candidate Staleness

Discard the candidate and generate from the current state. Never patch prose from a stale branch onto current authority.

### Provider Outage

Use bounded retry and configured provider fallback. Preserve the pending player decision and show a recoverable Web state. Do not submit the same choice twice.

### Revision Conflict

Reload the public projection and current decision. If the command receipt is complete, replay its committed result. If the client is stale, do not resubmit a semantically different command under the old key.

## 14. Evaluation

### 14.1 Deterministic Tests

Test:

- fact, knowledge, and belief isolation;
- semantic relationship evidence;
- promise deadlines and payoff events;
- choice conflict rules;
- immediate response linkage;
- no-op rejection;
- completion evidence compilation and evaluation;
- dynamic ending termination;
- candidate state hashes and staleness;
- failure without partial commit;
- idempotent replay;
- removal of obsolete runtime paths.

### 14.2 Automated Play Policies

The causal-slice release suite runs each policy over 10 fixed seeds, for 60 total runs. Each turn has a 30-second generation timeout and each run has a 5-minute wall-clock timeout. The expanded 30-45 minute suite uses the same policy set over 20 fixed seeds, for 120 total runs, with a 12-minute per-run timeout.

Run these policies:

- always trust Alice;
- prefer evidence;
- try to protect everyone;
- repeatedly change stance;
- remain distant;
- choose randomly.

Measure:

- completion rate;
- generation acceptance and repair rates;
- wait latency;
- immediate choice acknowledgment rate;
- promise payoff timeliness;
- no-op rate;
- completion evidence integrity;
- event and ending divergence between strategies;
- knowledge and contradiction violations;
- number of meaningful turning points.

### 14.3 Human Playtest

The first internal round uses 3-5 players. Each completes a run and answers:

- Did every choice have an understandable meaning?
- Did characters remember specific player behavior?
- Which choice was hardest to make?
- Which consequence was surprising but fair?
- Where did the story become boring, repetitive, or directionless?
- Would the player immediately replay from another stance?

## 15. Release Gates

The 10-15 minute causal slice is complete only when:

- it plays from start to dynamic ending on Web;
- every decision moment presents sibling options with distinct value conflicts;
- the next performance visibly acknowledges every choice;
- every established stance is challenged and produces a committed consequence within the configured one-to-three-decision deadline;
- at least one promise receives a payoff;
- characters refer to specific committed relationship history;
- pass/fail evaluation cites real event evidence;
- the normal path has no noticeable generation pause with warmed candidates.

The 30-45 minute vertical slice is releasable only when:

- all 120 automated release runs terminate within their per-run timeout;
- across at least 200 consecutive production-contract candidate requests, the configured normal model has at least 95% direct-or-one-repair acceptance and at least 85% direct acceptance;
- no completion requirement lacks deterministic evidence;
- no obsolete mutation path remains;
- every human tester finishes a run;
- most testers name at least two consequences caused by their actions;
- most testers identify at least one genuinely difficult choice;
- most testers want to replay or express specific curiosity about another stance.

## 16. Implementation Sequence

1. Freeze a gameplay evaluation baseline from current saved openings and `/turns` behavior.
2. Add the production-model feasibility harness and select a model configuration that passes the architecture gate.
3. Introduce dramatic state and semantic event contracts behind the existing store/reducer boundary.
4. Compile machine-verifiable completion requirements and reject empty evidence rules.
5. Implement dramatic assignments, choice contracts, promise scheduling, and no-op validation.
6. Replace the production turn orchestrator with the unified drama turn service and disable all old mutation routes in the same release.
7. Implement state-bound opening and next-choice candidate generation with full consumption-time revalidation.
8. Update the Web client for immediate playback, semantic ending trace, and basic fixed assets.
9. Build the outcome-invariant causal slice and pass deterministic, automated, and human gates.
10. Expand the same pack and engine to 6-10 choices without adding cafe-specific engine branches.
11. Delete unreachable legacy modules and correct all CLI, API, tests, and documentation.

### Development Data Migration

This repository has no production deployment or compatibility commitment for existing local sessions. The dramatic-state schema therefore uses an explicit development reset at the migration boundary:

- bump `story_schema_version` and `candidate_cache_version`;
- refuse to load databases with the old schema and print the exact reset command;
- delete or archive local development SQLite files only through an explicit developer command, never automatically at application startup;
- invalidate all persisted pack and pre-generation caches when any bound version changes;
- keep event upcasters out of the first vertical slice.

If real user saves exist before release, this policy must be replaced by a separate migration design before deploying the new schema.

Detailed task decomposition belongs in the implementation plan after this design is reviewed.

## 17. Explicit Non-Goals

This design does not include:

- author marketplace or publishing workflow;
- generic visual pack editor;
- accounts, social systems, or cloud saves;
- support for 120-minute stories;
- runtime-generated images;
- voice, Live2D, or a native client;
- multiple simultaneously autonomous character agents;
- fixed route trees or author-written future beats;
- a requirement to generate every line after the player clicks.

## 18. External Reference Conclusions

The external review included mature authored narrative tools and recent LLM interactive-fiction projects.

- Ink and Yarn Spinner demonstrate that choices must be engine-level events that change variables and issue commands, even though this project will not adopt their fixed branching authoring model.
- Recent fact-backed LLM engines reinforce the value of typed authority, bounded proposals, clone-first commit, and evaluation across fixed player strategies.
- Multi-agent visual-novel generators are useful for offline asset or story production, but their pre-generated DAGs conflict with this product's requirement that authors cannot prescribe future routes.
- Autonomous-character projects show that persistent character state matters, but also expose long latency and coordination costs. The vertical slice therefore models character meaning in state without requiring one online agent per character.

The relevant lesson is not to imitate a repository's architecture. It is to preserve deterministic story truth while making choices, remembered consequences, and dramatic payoff first-class engine contracts.
