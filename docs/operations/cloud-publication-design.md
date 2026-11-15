# Cloud publication design

## Problem

The dashboard is a static Vite site on GitHub Pages. Its current deployment workflow publishes both `dev` and `main` to one URL, but the two branches use separate concurrency groups and the build always uses tracked `public/data`. This branch replaces that path with one serialized publisher, approved-source adapters, GitHub compare-and-swap checkpoints, immutable generations, and a browser that pins one generation. The repository's default branch is `main`, so the new schedule will not run until this workflow reaches `main`.

The owner confirmed that the public site is noncommercial. That permits Coin Metrics Community data under CC BY-NC 4.0 with attribution and modification disclosure. It does not resolve the rights for the existing Yahoo, FRED service, CoinGecko, FMP, RSS, or other source data.

## Usage (caller's view)

One Pages workflow owns scheduled refreshes and code deployments. It requests a refresh every 15 minutes and deploys code pushes to `dev` or `main`. GitHub can delay or drop a scheduled run, and sources update at their own cadence.

```sh
python -m pipeline.cloud_refresh --mode scheduled --app-revision "$ACTIVE_APP_SHA"
python -m pipeline.cloud_refresh --mode code-only --app-revision "$EVENT_SHA"
```

The scheduled mode restores the prior validated generation and provider checkpoints, refreshes only sources allowed by the source policy, recalculates risk, and validates a candidate. The code-only mode verifies and restores the live generation, then packages it with the pushed application revision without changing its data timestamps. An explicitly provisioned empty checkpoint bootstraps from approved sources when no deployment exists; missing or corrupt checkpoint files stop the run. Preflight requires the event revision to remain the head of `main` or `dev`, and the deploy job checks once more immediately before deployment. The first Pages publication requires `PAGES_LEGACY_BOOTSTRAP_CONFIRMED=true` whether known legacy files are detected or not, because a static site cannot be exhaustively listed from the runner. Existing immutable generations without matching checkpoint state are never auto-imported. Neither mode calls a provider whose publication rights are unresolved.

The workflow records a deployment attempt before it asks GitHub Pages to deploy. After deployment, it verifies the manifest and generation that Pages serves before it marks that attempt deployed.

## Shape

The main data shapes are an observation, a source checkpoint, a data generation, and a deployment attempt.

```python
@dataclass(frozen=True)
class SourceObservation:
    source_id: SourceId
    value: JsonValue | None
    license_id: LicenseId
    observed_at: datetime | None
    fetched_at: datetime | None
    status: ObservationStatus

@dataclass(frozen=True)
class DataGeneration:
    generation_id: Sha256
    parent_id: Sha256 | None
    producer_revision: GitSha  # app and pipeline commit used for this build
    created_at: datetime
    file_hashes: Mapping[PublishedPath, Sha256]

@dataclass(frozen=True)
class DeploymentAttempt:
    attempt_id: Uuid4
    attempt_number: int
    generation: DataGeneration
    artifact_id: PositiveInt
    artifact_digest: Sha256
    workflow_run_id: PositiveInt
    parent_attempt_id: Uuid4 | None

@dataclass(frozen=True)
class CloudState:
    source_checkpoints: Mapping[SourceId, CheckpointRef]
    deployed: DeploymentAttempt | None
    candidate: DeploymentAttempt | None
    abandoned: DeploymentAttempt | None
```

`SourceObservation.observed_at` comes from the source. `fetched_at` comes from the collector. A missing source has neither timestamp. The source policy registry is the only allowlist for data collection and public publication. It maps each source to its license, attribution, permitted use, cadence, and dataset paths.

`pipeline/cloud_refresh.py` restores and validates the deployed generation, runs the existing collector in approved-only mode, and returns an isolated candidate. `pipeline/cloud_publication.py` owns the publication state machine. It reconciles interrupted attempts, binds one immutable Pages artifact to a candidate, and marks it deployed only after a live readback verifies the pointer, manifest, and every data-file hash.

`pipeline/storage/cloud_bls.py`, `pipeline/storage/cloud_treasury.py`, and `pipeline/storage/publication_checkpoint.py` keep durable provider and deployment records on the dedicated `cloud-state` branch. They build on `GitHubCheckpoint` and its expected-head compare-and-swap. BLS and Treasury reservations reach the branch before their provider requests. Coin Metrics uses the provider observation timestamp and the noncommercial license disclosure.

`pipeline/publication.py` owns immutable data generations and local hash validation. `PagesGenerationRestorer` imports the live generation and checks it against the deployed checkpoint. Code-only publication accepts a `GenerationSource` directory bound to its manifest digest, verifies each copied file against that manifest, then stages the original bytes under a new application revision. It does not rerun age checks or rewrite data timestamps. Collection publication still requires the candidate validator. Both operations use the same stale-parent check, immutable installation, and atomic pointer switch; retrying the same code-only input after a lost acknowledgement returns the same pointer. The artifact retains the current generation and its immediate parent so a browser can finish in-flight requests during a deployment. The client checks for an update when it regains focus and reloads onto the new generation. It does not need permanent generation retention.

One workflow owns every Pages write. Its global concurrency group includes `dev`, `main`, scheduled, and manual runs, with cancellation disabled and a bounded queue. A scheduled workflow starts from `main`, then reads the deployed producer revision from the publication checkpoint and builds that exact revision. This prevents a scheduled `main` run from replacing a newer `dev` application. A code push changes the application revision to the event commit only while that commit remains the head of its publishing branch; preflight and the deploy job both check this. A push to `dev` intentionally publishes to the shared production URL as documented in ADR-0007, so a newer push to either branch may replace the app version published by the other branch. Code-only publication restores the current checked generation before building, so it does not revert data.

The generation manifest's `producer_revision` is the single source of truth for both the application and pipeline commit. A scheduled run checks out the deployed generation's producer revision before it collects or builds. A code push creates a new manifest with the new producer revision, even when the observation files stay unchanged. Each deployment attempt has its own UUID and sequence number.

The publisher validates every data file with the Python and Node contracts before preparing a candidate. It downloads the exact immutable Pages artifact by ID, fails on a digest mismatch, and checks the tar contents against the build and generation manifest. It then deploys that artifact and reads the Pages pointer, manifest, and file hashes. A retry marks the candidate deployed only when Pages serves that exact generation. It abandons a failed attempt only after the prior workflow has ended and Pages still serves the previous verified generation. Unknown or stale state stops publication and preserves the last verified site.

The first generation contains approved-source observations and explicit missing envelopes for datasets without an authorized source. The current contract requires each dataset file, but its schemas permit empty rows and missing freshness states. The risk model may emit an insufficient-evidence result. The UI hides the aggregate score and shows missing indicators. The cloud path must not copy historical source files from `public/data` into the first approved generation. It must refuse to bootstrap if no approved provider supplies an indicator that the current risk writer can serialize.

The workflow exposes scheduled, code-only, and manual modes. The refresh module hides provider cadence, source filtering, risk inputs, and candidate validation. GitHub Actions API and Pages transport shapes stay inside their adapters. One source policy controls collection and publication, so adding a provider cannot silently add a second allowlist.

## Synthesis decision

Candidate A is the base because it models deployment attempts, durable checkpoints, uncertain outcomes, and content retention. Candidate B's live Pages readback is retained as a transport mechanism, but the checkpoint remains authoritative; a valid older manifest cannot substitute for the generation recorded as deployed. Candidate C's source-policy-first candidate generation is retained, while its proposal to make `dev` CI-only is rejected because the user selected the existing shared-site behavior recorded in ADR-0007.

The independent judge recommended A with these corrections. It found that all candidates missed the active application revision, that A did not distinguish app and data identity, and that B treated live readback as stronger evidence than it is. The judge also recommended bounded generation retention with reload-on-focus over a new content-addressed client protocol. See the three candidate files and judge report under `/tmp/arena-cloud-publication/` for the full comparison.

## Tradeoffs accepted

- We accept an initially incomplete market view in exchange for omitting source payloads whose public redistribution rights remain unknown.
- We accept GitHub's best-effort scheduler in exchange for a zero-cost cloud runner. A 15-minute cron requests an attempt; it does not guarantee exact execution time or fresh observations.
- We accept a public cloud-state branch that contains only approved source snapshots and control records in exchange for durable request budgets and recovery across runners.
- We accept keeping the current and immediately previous generations in each Pages artifact. The browser reloads when it sees a newer generation and when a background tab regains focus.
- We accept failing a code-only deployment when the checked publication state cannot be restored. Publishing the checked-in `public/data` as a fallback could roll back data and restore unreviewed payloads.

## Alternatives considered

- Separate scheduled and code-deploy workflows. They would both write the same Pages site and would need a shared concurrency group, so one workflow has clearer ownership.
- Use the live Pages pointer as the only publication record. A cached but valid older generation can pass local hash checks, so the durable checkpoint must name the expected generation.
- Keep every full generation forever. At 96 scheduled runs per day, that has unbounded static-site growth. Keep only generations needed by active requests and use the client's reload check.
- Commit every complete data generation to a state branch. This ties public site history to every polling attempt and grows the repository. The state branch stores source checkpoints and attempt records instead.
- Run the existing `--full` command on every tick. It calls providers whose redistribution terms are unresolved and fetches daily or monthly observations at intraday cadence.
- Make `dev` CI-only. ADR-0007 records the user's choice that `dev` and `main` share the Pages site.

## Implementation reconciliation

The refresh entrypoint accepts `scheduled` and `code-only`, an application revision, and a Pages site URL. An empty, explicitly provisioned publication state is the only bootstrap state. Both modes collect approved sources into a new isolated directory when no deployment exists; neither reads the repository's old `public/data`. A missing or corrupt publication or provider checkpoint is a refusal, not bootstrap. Pending candidates require reconciliation first. A confirmed abandoned attempt remains in the audit state until the next reservation. When a deployment exists, scheduled collection requires its producer revision; code-only accepts the pushed event revision and verifies that checkout.

`PagesGenerationRestorer(base_url)` reads `/data/current.json`, checks it against the deployed checkpoint, and downloads the named manifest and every listed file. The manifest digest, generation identity, producer revision, parent, and file hashes must match before the data can be restored. Code-only creates a local candidate from those verified bytes without calling providers or rewriting their observation, fetch, or generation timestamps. `CloudPublisher` records artifact ID, digest, workflow run, and attempt identity before deployment. It confirms the public generation before marking the attempt deployed.

The deployed generation's producer revision is the active app revision for scheduled runs. The `cloud-state` branch was provisioned on 2026-11-15 with empty publication, BLS, and Treasury ledgers and protected against force pushes and deletion. The Pages schedule is defined in this branch but cannot run until the workflow reaches the repository's default branch, `main`.

The local Treasury snapshot defaults to a 15-minute current-year poll. The cloud wrapper reads the Treasury cadence from `config/public_sources.yaml` and persists each pending request to the shared checkpoint before the provider call. The cloud-state branch is provisioned; its first real reservation and collection remain unverified.

The current `MacroCollector` falls through to FRED for any series not supplied by BLS or Treasury. The cloud source policy must prevent that fallback. The provider registry must contain only Coin Metrics for crypto, and unsupported dataset collectors must return missing data without contacting their existing providers. `write_macro_history()` now writes empty group bundles and a full roster manifest even when no series returns rows, while retaining rows already in the local archives. A zero-feed run still cannot produce a risk score and the pipeline exits unsuccessfully.

## Collection health and macro history contract

Freshness stays the public availability state. An intentionally omitted dataset remains `missing` and carries the closed reason code `source_policy_excluded`. Only that code lets `dataset_health()` classify the current outcome as skipped. `not_collected_this_run`, absent or old records, provider failures, and other missing outcomes remain failed. A missing `input_dataset_unhealthy` output is degraded only in a full run, where the source inputs are in scope. A fact-layer-only rebuild still fails when its inputs are unhealthy. Collectors provide the reason code and bounded English detail. Callers do not infer policy from detail text.

The macro history index contains every series in `SERIES_GROUPS`. It records unavailable indicators with a zero count and null observation dates. A null latest value does not remove older archived observations. Group bundles omit zero-count series. The writer creates each group bundle even when it has no rows. The client contract requires `history/macro/index.json` and every 30d and 90d group bundle. `scripts/validate-json.mjs` checks the manifest against `latest/macro.json`. It checks group ownership, row counts, dates, and bundle consistency. Missing or malformed files fail validation. `scripts/stage_publication.py` runs both validators before publication. A rejected candidate leaves the current pointer unchanged.

The code-only path cannot call `stage_candidate()` on a prior generation because that validator treats old envelope `generated_at` values as blocking warnings. It must reuse an already-validated generation without rewriting its data timestamps.

## Open questions and risks

- The user confirmed noncommercial use, but the public history still contains data from sources whose redistribution rights have not been confirmed. The first scheduled generation must omit those historical payloads.
- GitHub schedules can be delayed or dropped. The dashboard must show the source observation time, not the schedule time, as data freshness.
- The cloud-state branch is initialized with empty checkpoint files and protected against force pushes and deletion. The first real reservation and publication still need a successful cloud run.
- A public Pages readback may be temporarily stale after deployment. The publisher must compare it with the reserved attempt and wait or reconcile rather than infer success.
- Equities, A-shares, commodities, news, and event data remain missing until an approved free source is found or a separate license is obtained.
- The GitHub runner has not executed the publisher. The schedule is present in this branch but remains inactive until the workflow reaches `main`. Run the manual cloud path and inspect the artifact and Pages readback before promoting it.

## Provision and verify the publisher

Run the manual cloud workflow from `dev` and inspect provider coverage, artifact hashes, recovery behavior, and Pages readback before promoting the workflow to `main`.
