# Dashboard data, UX, and motion overhaul

## Definition of done

- Every newly displayed observation has a source, period/time, unit, attribution, and a documented grant that permits this dashboard's public redistribution.
- Existing empty or stale fields either gain such an observation or keep a concise, specific reason. Personal-use, unknown-license, or third-party-restricted market data stays unpublished.
- The eight requested UX rules are applied consistently across Overview, Macro, Equities, Sectors, News, Calendar, Risk Lab, and Status.
- State changes use one restrained motion system, remain interruptible, respect reduced motion, and do not shift surrounding content unexpectedly.
- The production build, focused suites, UI journeys, data validation, and independent review pass on the exact PR head.
- GitHub Pages publishes the merged candidate and the live current pointer and data are read back.

## Phase A: Frame

- [x] Read the Principles section of the poteto-mode skill.
- [x] State the goal and delivery boundary.
- [x] Confirm current branch is clean and no PR is open.
- [x] Capture route, data, and motion baselines before changing code.
- [x] Inventory user-visible data gaps and source-rights evidence.
- [x] Define exact implementation units and merge predicate.

## Phase B: Design the workflow

- [x] Keep the existing grant -> provider -> collector -> schema -> page architecture.
- [x] Add only first-party or explicitly reusable free datasets with useful coverage.
- [x] Keep market prices absent where redistribution rights are not explicit.
- [x] Use the shared theme and route state for consistent, accessible motion.
- [x] Deliver the source/data unit before the cross-page UX/motion unit when their checks permit.

## Phase C: Run the loop

- [x] Implement and verify each source unit against a live first-party endpoint.
- [x] Validate snapshots and source-policy boundaries after each data unit.
- [x] Implement consistent UX states and reversible controls.
- [x] Apply one motion language to route and in-page state transitions.
- [x] Check desktop, narrow viewport, light/dark, and reduced-motion behavior.

## Phase D: Keep the audit trail

- [x] Record choices and unit outcomes in the append-only TSV.
- [x] Run the pstack no-comments pass on the final diff.
- [x] Audit the trail against the run; the first reviewer found and logged the equity empty-state defect, a two-file snapshot hash mismatch, and an overclaim about Risk Lab chart visualization. These are fixed or scoped in the proof.

## Phase E: Verify and ship

- [x] Run CI-equivalent frontend checks; all passed, with an ECharts bundle-size warning.
- [x] Drive all eight pages and key state changes in the browser.
- [x] Measure route transitions and long-page scroll responsiveness: no stable-route layout shifts or long tasks; mean frame 16.69 ms, p95 18.5 ms, max 19.4 ms.
- [x] Obtain a final independent diff and live-UI review after the Equities fixes (PASS by gpt-6.1-terra).
- [ ] Open a ready PR, merge on a verified head, and confirm live Pages data.

## Follow-up after live deployment

- [x] Inspect the actual deployed Chinese News route and translation metadata.
- [x] Preserve English headlines and summaries with an explicit source-language label when translations are missing.
- [x] Verify all-missing translation fallback at 360px and run frontend checks.
- [x] Obtain final independent review; open a PR, merge after required checks, and verify the Pages deployment.
