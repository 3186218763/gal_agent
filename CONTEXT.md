# Dynamic Galgame

This context describes an authored interactive story whose moment-to-moment path and ending are generated during play, while the work remains bounded and judgeable.

## Language

**Script Pack**:
The authored source of a work: its story premise, fictional boundaries, dramatic concerns, and completion criteria. It defines a possibility space rather than enumerating every plot branch or ending.
_Avoid_: Fixed script, route tree

**Author Canon**:
The immutable rules, history, core characters, core places, and established facts that define a work's fictional identity. A Playthrough may develop the fiction but cannot revise this foundation.
_Avoid_: Prompt context, lore notes

**Open Question**:
An author-declared uncertainty whose answer may become true during a Playthrough within authored candidate or generation bounds. It remains possible until supported and committed, after which its answer is irreversible in that Playthrough.
_Avoid_: Random fact, hidden preset answer

**Emergent Detail**:
A locally invented descriptive element that does not initially affect continuing story causality. If later events depend on it, it must become an explicit part of Committed History.
_Avoid_: Canon, disposable fact

**Dramatic Obligation**:
An authored situation, confrontation, or payoff that a Playthrough must meaningfully address before it can converge early. It constrains what the story must deal with without prescribing a fixed Scene, order, or outcome.
_Avoid_: Plot beat, scripted event

**Obligation Disposition**:
The authored convergence requirement for a Dramatic Obligation or Open Question: it must be resolved, must be meaningfully addressed but may remain open, or is optional. A deliberate open result is distinct from a forgotten obligation.
_Avoid_: Complete flag, plot cleanup

**Dramatic Goal**:
A player-facing statement of what a work asks the player to engage with. It communicates the meaning of a Completion Contract without exposing internal evidence formulas or thresholds.
_Avoid_: Quest objective, score condition

**Dynamic Ending**:
A terminal story outcome created from the particular history of one playthrough. Its exact semantics and presentation do not have to belong to a finite author-written list.
_Avoid_: Ending route, preset ending

**Ending Integrity**:
The requirement that a Dynamic Ending follows from committed history, answers the playthrough's central dramatic question, and accounts for its significant promises and threads without violating the work's fictional boundaries.
_Avoid_: Ending eligibility

**Bounded Convergence**:
The guarantee that every playthrough is driven toward a terminal outcome within the work's authored experience bounds. The bound applies to the length of the playthrough, not to the number of possible endings.
_Avoid_: Finite endings, fixed routes

**Required Convergence**:
The state reached at the authored length limit, where no further ordinary Story Segments may be added and the Playthrough may only attempt a Dynamic Ending with Ending Integrity.
_Avoid_: Forced ending, timeout ending

**Completion Contract**:
The author-defined dramatic evidence required for a playthrough to count as having meaningfully completed the work. It evaluates the path taken and is distinct from merely reaching a Dynamic Ending.
_Avoid_: Ending condition, route condition

**Completed Playthrough**:
A playthrough whose committed history satisfies the work's Completion Contract. Every playthrough reaches a Dynamic Ending, but a playthrough may end without being completed.
_Avoid_: Good ending, winning route

**Opening Segment**:
The first Story Segment of a Playthrough, preceding any Player Choice. Because no player action precedes it, it is identical across all Playthroughs of the same Script Pack Version and is pre-generated and cached at pack scope.
_Avoid_: Intro cutscene, loading screen

**Player Choice**:
A presented action with stable, machine-readable intent and any associated stance, risk, or potential cost. Its concrete consequences may be generated, but subsequent play cannot ignore or reverse what the player chose.
_Avoid_: Prompt suggestion, dialogue reply

**Story Segment**:
The stretch of story between two Player Choices, or between a Player Choice and a Dynamic Ending. It may contain multiple Scenes and always ends at a point that requires the player or ends the playthrough.
_Avoid_: Turn, response

**Committed Segment**:
An accepted Story Segment preserved with its final text, Performance Cues, structured story events, Choice Meaning, causal references, and Script Pack Version. It is the complete replayable record of what the player experienced between decisions.
_Avoid_: Model response, transcript chunk

**Scene**:
A continuous dramatic situation with a coherent place, participants, and immediate purpose. Several Scenes may form one Story Segment without requiring player input between them.
_Avoid_: Turn, text block

**Choice Meaning**:
The intent, target, stance, accepted risk, potential obligation, and dramatic conflict expressed by a Player Choice. It becomes part of Committed History when selected, before its concrete consequences are resolved.
_Avoid_: Choice result, inferred intent

**Pending Consequence**:
The recoverable state after Choice Meaning has been committed but before its Story Consequence and following Committed Segment have been accepted. The Playthrough cannot accept another Player Choice while this consequence remains pending.
_Avoid_: Failed turn, uncommitted choice

**Story Consequence**:
A bounded change to fictional state caused by committed Choice Meaning. Its concrete form may be proposed dynamically, but it becomes authoritative only after its causal relationship and authored boundaries have been accepted.
_Avoid_: Generated prose, model interpretation

**Causal Trace**:
The chain from committed Choice Meaning through one or more accepted Story Consequences to later dramatic development and, where relevant, the Dynamic Ending. It is the evidence that a Player Choice affected the Playthrough rather than merely changing generated wording.
_Avoid_: Branch, text difference

**Semantic Judge**:
An independent evaluator that identifies high-risk meaning conflicts in proposed story content, such as canon contradiction, knowledge leakage, choice reversal, boundary violation, or missing ending integrity. It reports structured findings but cannot create prose or mutate story state.
_Avoid_: Co-writer, deterministic validator

**Attributed Assertion**:
A proposition expressed by a character together with whether it represents knowledge, belief, suspicion, deliberate deception, or misunderstanding. It records what was communicated without treating every statement as world truth.
_Avoid_: Fact, untyped dialogue claim

**Committed History**:
The authoritative sequence of accepted story events for a playthrough. Fictional truth and consequences derive from this history; generated prose and technical failures are not authoritative events by themselves.
_Avoid_: Chat history, model memory

**Playthrough**:
One irreversible Committed History from the opening to a Dynamic Ending. A player explores another possibility by starting a new Playthrough, not by replacing an earlier Player Choice.
_Avoid_: Route, save branch

**Protagonist**:
The author-defined player character of a work, including their established identity and behavioral boundaries. Player Choices develop the Protagonist's stance and relationships without replacing that authored identity.
_Avoid_: Player profile, custom persona

**Performance Cue**:
An instruction to present an author-supplied background, character pose, expression, illustration, sound, music, or transition. Dynamic narration may select valid cues but cannot invent unavailable work assets.
_Avoid_: Generated asset, prose direction

**Work Boundary**:
An author-defined restriction on what may occur or be portrayed in a work, layered beneath non-overridable platform safety limits. Player action and narrative convenience cannot waive it.
_Avoid_: Prompt warning, optional filter

**Dramatic Review**:
A player-facing account of which Dramatic Goals a Playthrough meaningfully satisfied or left unresolved. It explains completion in the work's language without exposing engine thresholds, event identifiers, or model scores.
_Avoid_: Score breakdown, debug trace

**Engine Work**:
A Work used to prove and calibrate the platform's authoring and runtime contracts before broader publication. It must meet the same player-facing integrity standard as other Works even when its primary purpose is validation.
_Avoid_: Toy demo, fixture

**Script Pack Version**:
An immutable published form of a Script Pack to which a playthrough is permanently bound. A later author revision is a different version and cannot silently change an existing playthrough.
_Avoid_: Latest pack, mutable script

**Work**:
An authored dynamic Galgame across all of its Script Pack Versions. A Work may stop accepting new Playthroughs without invalidating existing histories bound to an earlier published version.
_Avoid_: Pack, game session

**Playable Version**:
A validated Script Pack Version available for author testing but not public discovery or new public Playthroughs. It exercises the same runtime contract as a published version.
_Avoid_: Development server, draft preview

**Published Version**:
An immutable Script Pack Version available to players for new Playthroughs. Withdrawing it stops new starts but preserves every Playthrough already bound to it.
_Avoid_: Latest draft, live document

**Authoring Assistant**:
An Agent that proposes structured changes to a draft Script Pack for explicit author review. Its suggestions have no authority until the author accepts them and it cannot publish a Work.
_Avoid_: Co-author, automatic publisher
