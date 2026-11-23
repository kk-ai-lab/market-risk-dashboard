import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { EnergyContextCard } from "@/components/energy/EnergyContextCard";
import type { EnergyEnvelope } from "@/schemas";

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("zh-CN");
});

function energyEnvelope(): EnergyEnvelope {
  const now = new Date();
  const latest = now.toISOString().slice(0, 10);
  const previous = new Date(now.getTime() - 7 * 86_400_000).toISOString().slice(0, 10);
  const timestamp = now.toISOString().replace(/\.\d{3}Z$/, "Z");
  return {
    generated_at: timestamp,
    schema_version: "1.0.0",
    source: "eia",
    source_updated_at: null,
    freshness_status: "fresh",
    data_quality: 1,
    provenance: { provider: "eia", used_fallback: false, from_cache: false },
    payload: {
      series_id: "WCESTUS1",
      unit: "thousand_barrels",
      observations: [
        { week_ending: previous, released_on: null, value: 409_500 },
        { week_ending: latest, released_on: null, value: 412_000 },
      ],
      collection_status: "available",
      observation_age: "within_window",
      last_attempt_at: timestamp,
      last_successful_fetch_at: timestamp,
    },
  };
}

describe("EnergyContextCard", () => {
  it("shows weekly inventory, change, retrieval, and EIA attribution", async () => {
    await i18n.changeLanguage("en");
    render(<EnergyContextCard dataset={energyEnvelope()} contextOnly />);

    expect(screen.getByText("Energy supply context")).toBeInTheDocument();
    expect(screen.getByText("412,000 thousand barrels")).toBeInTheDocument();
    expect(screen.getByText("+2,500 thousand barrels")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();
    expect(screen.getByText("Within 14 days")).toBeInTheDocument();
    expect(screen.getByText("Context only; excluded from the risk score and its coverage.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Source: EIA · WCESTUS1" })).toHaveAttribute(
      "href",
      "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?f=W&n=PET&s=WCESTUS1",
    );
  });

  it("shows the last successful retrieval even when the source returned no rows", async () => {
    await i18n.changeLanguage("en");
    const dataset = energyEnvelope();
    dataset.payload.observations = [];
    dataset.payload.collection_status = "empty";
    dataset.payload.observation_age = "unknown";

    render(<EnergyContextCard dataset={dataset} />);

    expect(screen.getByText(/^Last successful retrieval:/)).toHaveTextContent(/2026/);
    expect(screen.getByText(/^Last check:/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Source: EIA · WCESTUS1" })).toBeInTheDocument();
  });

  it("keeps a degraded collection badge distinct from stale data", async () => {
    await i18n.changeLanguage("en");
    const dataset = energyEnvelope();
    dataset.payload.collection_status = "degraded";

    render(<EnergyContextCard dataset={dataset} />);

    const badge = screen.getByText("Refresh degraded");
    expect(badge).toHaveClass("border-border");
    expect(badge).not.toHaveClass("border-fresh-warn/40");
  });
});
