# Pages build version grounding

## User goal and release issue

The user wants cloud collection every 15 minutes, with later dashboard, data coverage, and risk-model work. The 15-minute workflow is meant to publish changing data from GitHub Pages. Independent spec review found that the current Pages status lookup cannot identify a scheduled candidate reliably.

## Evidence

- `.github/workflows/deploy-pages.yml` schedules `*/15 * * * *`. Push-triggered publication is enabled for `main` and `dev`; GitHub runs the schedule only from the default branch, which is still `main`.
- The workflow uses pinned `actions/deploy-pages` commit `d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e`.
- At that commit, `src/internal/context.js` assigns `process.env.GITHUB_SHA` to `buildVersion`; `action.yml` exposes no build-version input. Its entry point calls `core.getIDToken()`, creates the Pages deployment, polls status, and cancels on timeout or process signal.
- GitHub's Pages create-deployment API requires `pages_build_version` to be a unique string. The status endpoint accepts a Pages deployment ID or the commit SHA.
- A scheduled run on an unchanged branch keeps the same `GITHUB_SHA` while publishing a new data generation. A status read by that SHA can therefore refer to a different generation than the pending candidate.
- `pipeline.publication._stage_generation()` creates a 64-character SHA-256 generation ID from the immutable manifest, which includes `producer_revision`, `created_at`, and file hashes. New generated or code-only generations change the manifest identity.
- `pipeline.cloud_publication.CloudPublisher.reserve()` stores the exact artifact ID, generation ID, workflow run ID, digest, and a random UUID4 `attempt_id` before deployment. The deployment job receives artifact and generation IDs from `prepare` outputs.
- The deploy job already has `pages: write` and `id-token: write`. `actions/deploy-pages` currently discovers the named artifact itself; direct API deployment can use the already-verified artifact ID.
- The workflow's final `finish` step reads the public pointer, manifest, and files and advances checkpoint state only when the exact reserved generation is served.

## Current flow

1. The workflow serializes push, schedule, and manual runs.
2. `preflight` reads the publication checkpoint and Pages pointer.
3. Collection or code-only staging creates a new immutable generation and Pages artifact.
4. `reserve` records the candidate and artifact ID.
5. The deploy action uses `GITHUB_SHA` as `pages_build_version` and polls the SHA-keyed deployment status.
6. `finish` verifies the public pointer, manifest, and file hashes against the candidate.

## Required behavior

Each Pages build version must identify the exact immutable reserved build and remain stable across retries of that same candidate. Deployment must use the reserved artifact ID and GitHub OIDC token. If a POST outcome is ambiguous, the publisher must look up that candidate's build version and retain the pending checkpoint until the deployment status and public readback resolve it. A deployment API success alone cannot advance the checkpoint.

## Design options

1. Add a small Python Pages deployment adapter and CLI. It gets an OIDC token, creates a deployment with the reserved artifact ID and unique candidate version, polls and cancels by that version, and exposes only safe outputs. `CloudPublisher` reads status by the candidate version.
2. Put the deployment API and polling in a workflow script using an existing JavaScript action or shell. This avoids installing Python in the deploy job but moves deployment policy and tests into workflow code.
3. Vendor a pinned copy of `actions/deploy-pages` with a custom build-version input. This preserves upstream deployment behavior but creates a local fork to maintain and test.

## Remaining live-validation question

- Will GitHub Pages accept a valid commit SHA from the same repository's `cloud-state` branch as `pages_build_version` when it differs from the workflow's `GITHUB_SHA`? The API documents a unique string and accepts commit SHA for status lookup, but no live deployment has tested this combination.

## Current remote checkpoint

Read `refs/heads/cloud-state` at `65b1d30212ece6dda7d31a665499551a1eff3bc3` on 2026-11-17. `publication.json` is schema v1, has one deployed generation, and has no pending or abandoned candidate. This permits a backward-compatible decoder to read current state and create the next v2 reservation without migrating a pending legacy candidate.

The `dev` and `main` branches publish to the same Pages URL. A test deployment from `dev` would change the public site, so this run will not use it as a disposable API probe. The PR can document that the first approved Pages deployment is still the live contract check.

## Cross-judge synthesis

The read-only architecture judge selected the Python deployment adapter and rejected all three candidate sketches as written. The selected refinement stores the pre-reservation `cloud-state` commit SHA as `pages_build_version` in the same compare-and-swap write that reserves the candidate. This is unique per reservation while the checkpoint branch remains append-only, survives retries, is a commit SHA accepted by the status endpoint, and avoids requiring a UUID or 64-character hash to be accepted as a deployment ID.

The deployment adapter must also persist a fresh submission UUID before POST. Only the process that confirms its own newly written UUID may submit once. Later processes may inspect status or cancel, but may not issue another POST for that candidate. A missing status after a possible POST and the upstream `deployment_lost` status remain unresolved. Legacy v1 candidates have no candidate-specific Pages version. Only exact public readback can complete one; exact workflow evidence that the deploy step was skipped can establish that no deployment started. Historical SHA status cannot authorize abandoning a legacy attempted deployment.

The judge scored the unmodified candidates 19/25, 14/25, and 12/25. It scored the SHA and submission-claim refinement 22/25. The remaining uncertainty is whether Pages accepts a valid `cloud-state` commit SHA that differs from the workflow's `GITHUB_SHA`; the API documentation does not explicitly state that relation. The shared production site will not be modified just to test this contract.
