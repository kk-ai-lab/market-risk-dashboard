# Risk model version history boundary

## Architect phases

- [x] Ground: trace the risk history writer, daily history reader, trend calculations, and visible chart path.
- [x] Sketch: compare at least two version-boundary designs with typed row shapes and call sites.
- [x] Agree: proceed without a human checkpoint.
- [x] Implement: apply the selected boundary in code and reconcile the feature ADR.
- [ ] Scrap: reconsider the design if the implementation needs repeated exceptions.

## Arena phases

- [x] Frame: grade correctness, legacy compatibility, chart honesty, and API size.
- [x] Fan out: produce independent read-only design candidates in isolated `/tmp` directories.
- [x] Cross-judge: skipped; no configured runner was from a different model family.
- [x] Pick: compare candidates criterion by criterion.
- [x] Graft: carry only useful improvements into the selected design.
- [x] Verify: inspect bilingual history UI; legacy and gap rows are distinct, while versioned series cannot be proven from the old fixture.

## Work units

- [x] Implement and reconcile the versioned history shape.
- [x] Complete independent standards and spec reviews.
- [x] Inspect the bilingual UI against checked-in fixtures without rewriting data.
- [x] Deslop, run permitted non-test checks, and record their limits.
- [ ] Commit and push the feature branch; open or update the review PR to `dev`.
- [x] Keep the already-approved `dev` to `main` PR open; do not merge or deploy.

## Constraints

- No tests are added or run unless the user requests them.
- Do not rewrite `public/data/` fixtures or run the collection pipeline.
- Do not create a worktree; this feature branch is the sole write owner.

## Accepted comment fixes

- [x] Read the no-comments report and reject suppressions outside changed hunks.
- [x] Complete the no-comments review; it flagged two redundant docstrings and the formula docstring.
- [x] Compare local calculation shapes so the 15-month formula reads from code structure.
- [x] Remove redundant `read_prev_risk` and config-assertion test docstrings.
- [x] Remove the formula docstring after the code shape makes its behavior explicit.
- [x] Reconcile the formula organization with the existing ADR and local rationale.
- [x] Rerun permitted non-test checks: typecheck, py_compile, scoped Ruff, and diff whitespace check pass; broader Ruff reports three issues on unchanged lines.
