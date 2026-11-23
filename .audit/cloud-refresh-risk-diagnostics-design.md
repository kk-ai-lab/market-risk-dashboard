# Cloud risk-input diagnostics

## Problem

The hosted approved-source run rejects when every risk indicator is null. The current Actions summary shows the rejection code and post-rollback dataset health, but not whether the approved Treasury data reached the macro payload and risk model. The diagnostic must use data already collected, avoid score changes, and keep values, secrets, and provider text out of Actions logs.

## Usage

An operator should see a fixed diagnostic on the existing `cloud_refresh.failure` event:

```json
{
  "rejection_code": "risk_no_usable_indicators",
  "risk_input_diagnostics": {
    "treasury": {
      "status": "checkpoint_unavailable",
      "dgs2_observation_count": 0,
      "dgs10_observation_count": 0
    },
    "macro_dgs2_has_value": false,
    "macro_dgs10_has_value": false,
    "usable_indicator_count": 0,
    "configured_indicator_count": 19
  }
}
```

Counts and presence flags separate source snapshot availability from macro mapping and model output. `configured_indicator_count` comes from the scored result rather than a permanent hard-coded model size.

## Shape

Use frozen, strict `RiskInputDiagnostics` and `TreasuryInputEvidence` report models. The Treasury model has an enum `TreasuryRefreshStatus` and two bounded observation counts. The risk model adds two exact boolean macro-presence fields and bounded usable/configured indicator counts. Each count must be from 0 through 10,000. The refresh status has these values: `not_configured`, `complete`, `complete_degraded`, `no_observations`, `refresh_failed`, and `checkpoint_unavailable`. Snapshot errors map to `refresh_failed` without copying their text. A successful snapshot with partial coverage maps to `complete_degraded`. Zero rows with no recorded errors maps to `no_observations`. A refresh error may coexist with cached rows, so status and counts remain independent facts. `usable_indicator_count` cannot exceed `configured_indicator_count`; the matching no-usable-indicators rejection must report zero usable indicators.

Capture only the compact Treasury status/count summary when the refreshed snapshot is handed to `MacroCollector`. At the existing rejection branch, combine it with DGS2/DGS10 value presence in the macro payload and the actual indicator counts from `RiskModelResult`. Attach the typed summary to `RunRejection`, serialize it as an optional run-report field, and parse it into `CollectionSummary` only for the matching rejection code. The cloud layer emits only the validated model. Malformed or unknown optional evidence is omitted while the existing failure and rejection codes remain intact.

The data shape belongs to the run-report boundary (`pipeline/report/run_report.py`); `pipeline/run.py` owns the collected and scored facts; `pipeline/cloud_refresh.py` owns parsing and safe projection into Actions logs. This keeps the code path within those three modules and adds no provider calls, sidecar, or risk formula changes.

## Synthesis decision

Candidate A is the base because it has the smallest explicit report contract and the clearest strict parsing plan. Candidate C contributes the finite Treasury refresh outcome, including an explicit no-observations and not-configured state. Candidate B contributes snapshot-time counts and the configured indicator count from the actual model result. Observation dates were omitted because they do not decide the current source-versus-mapping question. Raw `provider_status` passthrough and an extra child sidecar were rejected because they expose unstable payloads or duplicate the existing report protocol.

Three candidate designs converged on the existing report-to-cloud-summary boundary. The independent judge scored A 22/25, B 19/25, and C 20/25, and agreed on A with the status and snapshot-summary grafts. The judge's model-family check was unknown because the parent model identifier was not exposed.

## Verification contract

Cover empty checkpoint state, failed refresh with retained rows, snapshot rows lost before macro mapping, macro values with no usable model indicators, malformed/oversized JSON fields, and no diagnostic leakage from raw provider fields. The next serialized GitHub Pages workflow is the runtime probe; local tests cannot prove Treasury's hosted response.
