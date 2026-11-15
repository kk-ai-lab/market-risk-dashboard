import { describe, expect, it } from "vitest";
import { z } from "zod";
import riskFixture from "../fixtures/risk.json";
import {
  CryptoAsset,
  CryptoDataset,
  CryptoEnvelope,
  DashboardEnvelope,
  DashboardPayload,
  DatasetFreshness,
  DomainStatus,
  FreshnessDocument,
  FreshnessReason,
  RiskDimension,
  RiskEnvelope,
  RiskModelResult,
  SourcesDocument,
} from "@/schemas/generated/contracts";
import { RiskTrendSlice } from "@/schemas/history";
import { dashboardFixture } from "./helpers/fixtureData";

// These two closed vocabularies are pinned to dev at 41d3bdd. The remaining fields reuse the
// generated shapes because they did not diverge; this catches exactly the old-client enum risk.
const LegacyReasonCode = z.enum([
  "ok", "no_rows_returned", "no_events_in_window", "provider_http_error",
  "provider_rate_limited", "provider_auth_failed", "provider_parse_error",
  "served_from_fallback", "served_from_cache", "cache_expired", "cache_invalid",
  "all_providers_failed", "not_collected_this_run", "interval_exceeded",
  "input_dataset_unhealthy",
]);
const LegacyRiskTrend = z.enum(["rising", "falling", "flat"]);
const LegacyRiskDimension = RiskDimension.extend({ trend: LegacyRiskTrend.default("flat") });
const LegacyRiskModelResult = RiskModelResult.extend({
  dimensions: z.array(LegacyRiskDimension).default([]),
});
const LegacyRiskEnvelope = RiskEnvelope.extend({ payload: LegacyRiskModelResult });
const LegacyDashboardEnvelope = DashboardEnvelope.extend({
  payload: DashboardPayload.extend({ risk: LegacyRiskModelResult }),
});
const LegacyCryptoAsset = CryptoAsset.extend({ updated_at: z.string().datetime() });
const LegacyCryptoEnvelope = CryptoEnvelope.extend({
  payload: CryptoDataset.extend({ assets: z.array(LegacyCryptoAsset).default([]) }),
});
const LegacyFreshnessReason = FreshnessReason.extend({ code: LegacyReasonCode });
const LegacyDatasetFreshness = DatasetFreshness.extend({ reason: LegacyFreshnessReason });
const LegacyDomainStatus = DomainStatus.extend({ reason: LegacyFreshnessReason });
const LegacyFreshnessDocument = FreshnessDocument.extend({
  datasets: z.record(z.string(), LegacyDatasetFreshness).default({}),
});
const LegacySourcesDocument = SourcesDocument.extend({
  domains: z.record(z.string(), LegacyDomainStatus).default({}),
});

describe("legacy mutable endpoint contracts", () => {
  it("accepts a complete risk snapshot and dashboard with old trend values", () => {
    const risk = structuredClone(riskFixture);
    risk.payload.evidence_state = "complete";
    const dashboard = structuredClone(dashboardFixture);
    dashboard.payload.risk.evidence_state = "complete";

    expect(LegacyRiskEnvelope.safeParse(risk).success).toBe(true);
    expect(LegacyDashboardEnvelope.safeParse(dashboard).success).toBe(true);
  });

  it("rejects unknown risk trends and new reason codes under the dev enums", () => {
    const risk = structuredClone(riskFixture);
    risk.payload.evidence_state = "complete";
    risk.payload.dimensions[0].trend = "unknown";
    expect(LegacyRiskEnvelope.safeParse(risk).success).toBe(false);

    const freshness = {
      schema_version: "1.0.0",
      updated_at: "2026-11-15T12:00:00Z",
      datasets: {
        equities: {
          status: "missing",
          reason: { code: "source_policy_excluded", detail: "not requested" },
          updated_at: "2026-11-15T12:00:00Z",
        },
      },
    };
    expect(LegacyFreshnessDocument.safeParse(freshness).success).toBe(false);
  });

  it("rejects nullable crypto timestamps under the dev contract", () => {
    const crypto = {
      generated_at: "2026-11-15T12:00:00Z",
      schema_version: "1.0.0",
      source: "coinmetrics",
      freshness_status: "fresh",
      data_quality: 1,
      provenance: { provider: "coinmetrics" },
      payload: {
        assets: [{ symbol: "BTC", name: "Bitcoin", price: 90000, source: "coinmetrics", updated_at: null }],
      },
    };

    expect(LegacyCryptoEnvelope.safeParse(crypto).success).toBe(false);
    expect(LegacyCryptoEnvelope.safeParse({
      ...crypto,
      payload: {
        assets: [{
          ...crypto.payload.assets[0],
          updated_at: "2026-11-15T12:00:00Z",
        }],
      },
    }).success).toBe(true);
  });

  it("parses projected legacy reason codes and complete history points", () => {
    const freshness = {
      schema_version: "1.0.0",
      updated_at: "2026-11-15T12:00:00Z",
      datasets: {
        equities: {
          status: "missing",
          reason: { code: "not_collected_this_run", detail: "source policy excludes" },
          updated_at: "2026-11-15T12:00:00Z",
        },
      },
    };
    const sources = {
      schema_version: "1.0.0",
      updated_at: "2026-11-15T12:00:00Z",
      domains: {
        market: {
          degraded: true,
          status: "missing",
          reason: { code: "input_dataset_unhealthy", detail: "translation incomplete" },
        },
      },
    };

    expect(LegacyFreshnessDocument.safeParse(freshness).success).toBe(true);
    expect(LegacySourcesDocument.safeParse(sources).success).toBe(true);
    expect(RiskTrendSlice.safeParse([
      { date: "2026-11-15", total_score: 55, evidence_state: "complete" },
    ]).success).toBe(true);
  });
});
