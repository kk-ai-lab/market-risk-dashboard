# BLS risk signal expansion

## Goal

Use already-approved, publicly redistributable BLS observations to improve the risk model without presenting revised history as point-in-time evidence. Preserve explicit source age and bilingual explanations. Work from `origin/dev` on a normal feature branch. Do not create a worktree or merge `dev` to `main`.

## Feature playbook

- [x] `how` over the affected subsystem.
- [x] `architect` for parallel design exploration.
- [x] Write the throughput checkpoint as four todo items. A dimension that genuinely does not apply (single file, no fan-out) keeps its item with `n/a: <reason>` rather than being dropped:
  - **Blocking first steps.** Gates run before fan-out.
  - **Independent workstreams.** Disjoint files, services, or layers parallelize. Shared writes serialize.
  - **Shared mutable state.** Default to splitting the target (the **separate-before-serializing-shared-state** principle skill). Serialize only for real invariants.
  - **Smallest safe decomposition.** If one worker is best, name why.
- [ ] Delegate code-writing to a subagent using your configured feature model (default in poteto-mode's Models section) with a specific scope (file paths, named data shape and its organizing structure per **principle-model-the-domain**, a state machine over scattered booleans, a table/registry over branching, a typed model over repeated shape assumptions, chosen before the delegate writes logic, and success criteria). Pick each subagent's reasoning level for the job, per the Subagents section. When the implementation admits multiple valid shapes (error handling, abstraction layer, test structure), delegate via the **arena** skill instead so the runners surface the alternatives and the cross-judge guards the pick. Mandatory: no skip-with-reason escape, and Laziness Protocol does not override it (the gain is review separation, not lines saved). A subagent forbidden to spawn satisfies this by owning the diff directly with the same review separation. No "standing by" reply that waits on a nested agent. **Give every file-writing delegate its own worktree** (spawn it with `isolation: "worktree"`, or hand it an exclusive branch), and do not write files or run a suite in a worktree a delegate still holds. On Claude Code, `isolation: "worktree"` branches from the remote default branch unless the `worktree.baseRef` setting is `"head"`. When the delegate builds on commits the default branch lacks, commit them, create its worktree with `git worktree add <path> -b <delegate-branch> HEAD`, and name that base commit in the brief. Fencing a file in the brief's prose is not a lock (**principle-separate-before-serializing-shared-state**). Comments per **Comments**. Surgical edits, re-ground against the source for upstream-derived files. Port shared-primitive improvements to all consumers and verify each.
- [ ] Verify on the matching surface. "Inconclusive" or wrong-surface is not a pass. Flag it.
- [ ] Rebase into small, ordered commits. Stack follow-ups.
  Use the **sequence-verifiable-units** principle skill, building, verifying, and committing each small unit before the next.
- [ ] If the design is contested, `interrogate` before shipping.
- [ ] Run **Opening a PR**.

## Architect phases

- [x] Ground
- [x] Sketch
- [x] Agree (no human checkpoint requested)
- [ ] Implement
- [ ] Scrap if the chosen architecture proves wrong

## Arena phases

- [x] Frame
- [x] Fan out: candidates 1 and 2 converged on direct UNRATE/PAYEMS rows; a separate candidate explores a derived Sahm-style signal
- [x] Cross-judge skipped: all configured seats inherit the same parent model family
- [x] Pick: candidate 3, a single Sahm-style history derived from BLS UNRATE
- [x] Graft: use candidate 2's backward-compatible `score_included` field and stale exclusion
- [x] Verify: the bilingual static route confirms legacy history treatment and explicit gap rows; the BLS row remains inconclusive because the checked-in fixture predates model 1.1.0.

## Arena artifact and grading criteria

- **Artifact.** A module map, data shape, signatures, caller usage, and rationale for one BLS-backed labor signal.
- **Criteria.** Preserve approved-source boundaries and the once-daily BLS cap; score a measure with an explicit and consistent definition; keep freshness and stale evidence honest; do not claim calibration from revised history; keep the UI bilingual and the contract backward-compatible; minimize ownership changes and review surface.
- **Initial comparison.** Candidates 1 and 2 both add unemployment and payroll as direct score rows. Candidate 1 models `evidence_eligible` and an optional score; candidate 2 models `score_included` and a compatibility default. These are materially different freshness contracts but their metric set and pipeline shape converge. Candidate 3 was added to compare a single derived labor-stress signal.
- **Cross-judge.** The configured arena panel resolves all seats to the parent model. Candidate reviews therefore have reduced model-family diversity; do not claim a cross-family judgment unless an eligible model becomes available.

## Throughput checkpoint

- **Blocking first steps.** Use the completed source, cloud, scoring, and UI traces. Confirm a new score input has an explicit definition, freshness treatment, history path, policy decision, and bilingual label before editing production code.
- **Independent workstreams.** Model and UI code share the `RiskIndicator` contract and translation registry. One implementation owner keeps those writes serialized; independent review follows a stable diff.
- **Shared mutable state.** The user prohibits worktrees. Root owns one feature branch and the shared checkout; any file-writing delegate must use an isolated clone or unique output directory, never a concurrent shared-branch writer.
- **Smallest safe decomposition.** First implement one source-backed labor signal with its current value, history, provenance, explicit freshness semantics, and bilingual row. Keep its formula and weight provisional. Add broader indicators only after this input has a credible history and review path.

## Work units

- [x] Read the current `origin/dev` state and preserve pre-existing untracked audit artifacts.
- [x] Trace cloud BLS cadence, transformations, persisted history, risk scoring, and UI localization.
- [x] Compare three designs, score them against the rubric, and save the chosen module map and rationale.
- [x] Create a normal feature branch from `origin/dev` without a worktree.
- [x] Implement the chosen signal and carry source month, fetch timestamp, and freshness into the risk UI.
- [x] Review the resulting diff independently and reconcile the architecture artifact.
- [x] Inspect the real changed artifact without running tests, which the developer instructions prohibit.
- [x] Push `features/bsl-risk-signal-expansion` and open PR #265 to `dev`. Do not merge `dev` to `main`.
