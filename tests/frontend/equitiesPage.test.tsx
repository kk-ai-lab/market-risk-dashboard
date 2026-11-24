import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it } from "vitest";
import i18n from "@/i18n";
import EquitiesPage from "@/pages/EquitiesPage";
import { fixtureForUrl, FIXTURE_MAP, installCustomFetch } from "./helpers/fetchMock";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><EquitiesPage /></MemoryRouter></QueryClientProvider>);
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  cleanup();
});

it.each([
  ["source_policy_excluded", "No free source with confirmed public redistribution rights"],
  ["provider_http_error", "Equity quotes are temporarily unavailable."],
])("explains empty equity quotes accurately for %s", async (reasonCode, message) => {
  const equities = structuredClone(FIXTURE_MAP["/latest/equities.json"]) as { payload: { assets: unknown[] } };
  equities.payload.assets = [];
  const freshness = structuredClone(FIXTURE_MAP["/metadata/freshness.json"]) as {
    datasets: { equities: { reason: { code: string; detail: string } } };
  };
  freshness.datasets.equities.reason.code = reasonCode;
  freshness.datasets.equities.reason.detail = "";
  installCustomFetch((url) => {
    if (url.endsWith("/latest/equities.json")) return { ok: true, body: equities };
    if (url.endsWith("/metadata/freshness.json")) return { ok: true, body: freshness };
    return fixtureForUrl(url);
  });

  renderPage();

  expect(await screen.findByTestId("equities-public-source-empty")).toHaveTextContent(message);
  expect(await screen.findByText("Memory proxy (MU / 000660.KS / 005930.KS)")).toBeVisible();
  expect(screen.queryByTestId("section-us")).not.toBeInTheDocument();
});

it("does not show an empty US equities section when only A-share assets are available", async () => {
  const equities = structuredClone(FIXTURE_MAP["/latest/equities.json"]) as { payload: { assets: Array<{ market: string }> } };
  equities.payload.assets = equities.payload.assets.filter((asset) => asset.market === "CN");
  installCustomFetch((url) => url.endsWith("/latest/equities.json") ? { ok: true, body: equities } : fixtureForUrl(url));

  renderPage();

  expect(await screen.findByTestId("section-ashare")).toBeVisible();
  expect(screen.queryByTestId("section-us")).not.toBeInTheDocument();
});
