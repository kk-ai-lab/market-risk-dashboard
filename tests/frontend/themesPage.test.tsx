import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, beforeEach } from "vitest";
import { fixtureForUrl, FIXTURE_MAP, installCustomFetch, installFixtureFetch } from "./helpers/fetchMock";
import i18n from "@/i18n";
import ThemesPage from "@/pages/ThemesPage";

/**
 * ThemesPage coverage (#93 — the page previously had none): renders the expanded theme
 * cards with labels (i18n), change, percentile band and constituent chips from sectors.json.
 * i18n is the real module (like router.test.tsx); the fixture drives the data.
 */

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><ThemesPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  installFixtureFetch();
});

describe("ThemesPage", () => {
  it("renders the 20-theme section with labels and constituent chips", async () => {
    renderPage();
    // Both fixture themes render with their i18n labels (locale detected as zh-CN here).
    expect(await screen.findByText("存储")).toBeInTheDocument();
    expect(screen.getByText("网络安全")).toBeInTheDocument();
    // Constituent chips are rendered from the payload.
    expect(screen.getByText("MU")).toBeInTheDocument();
    expect(screen.getByText("PANW")).toBeInTheDocument();
  });

  it("renders a percentile band for a computed percentile and warming-up for a short series", async () => {
    renderPage();
    await screen.findByText("网络安全");
    // memory percentile 90 → "Very High" band (zh: 极高).
    expect(screen.getByText("极高")).toBeInTheDocument();
    // cybersecurity percentile null with obs 30 → "warming up 30/100" (zh: 累积中 30/100).
    expect(screen.getByText("累积中 30 个观测")).toBeInTheDocument();
  });

  it("renders the commodities section from commodities.json (#118)", async () => {
    renderPage();
    // fixture commodities: GC=F gold / CL=F WTI crude → cards with zh names + symbol.
    expect(await screen.findByText("黄金")).toBeInTheDocument();
    expect(screen.getByText("GC=F")).toBeInTheDocument();
    expect(screen.getByText("WTI 原油")).toBeInTheDocument();
    expect(screen.getByText("CL=F")).toBeInTheDocument();
  });

  it("shows the safe not-configured EIA state when no API key is set", async () => {
    renderPage();
    expect(await screen.findByText("尚未配置")).toBeInTheDocument();
    expect(screen.getByText("美国原油周度库存")).toBeInTheDocument();
  });

  it.each([
    ["source_policy_excluded", "No sector or theme price data has an approved public redistribution source."],
    ["provider_http_error", "Sector and theme quotes are temporarily unavailable."],
  ])("explains empty sector quotes accurately for %s", async (reasonCode, message) => {
    await i18n.changeLanguage("en");
    const sectors = structuredClone(FIXTURE_MAP["/latest/sectors.json"]) as {
      payload: { sectors: unknown[]; themes: unknown[]; memory: unknown };
    };
    sectors.payload.sectors = [];
    sectors.payload.themes = [];
    sectors.payload.memory = null;
    const freshness = structuredClone(FIXTURE_MAP["/metadata/freshness.json"]) as {
      datasets: { sectors: { reason: { code: string; detail: string } } };
    };
    freshness.datasets.sectors.reason.code = reasonCode;
    freshness.datasets.sectors.reason.detail = "";
    installCustomFetch((url) => {
      if (url.endsWith("/latest/sectors.json")) return { ok: true, body: sectors };
      if (url.endsWith("/metadata/freshness.json")) return { ok: true, body: freshness };
      return fixtureForUrl(url);
    });

    renderPage();

    expect(await screen.findByText(new RegExp(message))).toBeInTheDocument();
  });
});
