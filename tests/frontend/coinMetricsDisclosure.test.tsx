import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { Footer } from "@/components/layout/Footer";
import { CryptoEnvelope } from "@/schemas";
import { FIXTURE_MAP } from "./helpers/fetchMock";
import ThemesPage from "@/pages/ThemesPage";
import StatusPage from "@/pages/StatusPage";

const state = vi.hoisted(() => ({ datasets: {} as Record<string, unknown> }));
vi.mock("@/hooks/useDataset", () => ({
  useDataset: (key: string) => ({ data: state.datasets[key], isLoading: false, isError: false, refetch: vi.fn() }),
}));
afterEach(cleanup);

it.each([["en", "Source observation", "reference rates"], ["zh-CN", "来源观测时间", "参考价"]])(
  "shows source timing, limitations and the license in %s",
  async (locale, observed, disclosure) => {
    await i18n.changeLanguage(locale);
    const crypto = CryptoEnvelope.parse(FIXTURE_MAP["/latest/crypto.json"]);
    crypto.freshness_status = "stale";
    crypto.payload.assets = [{ ...crypto.payload.assets[0], source: "coinmetrics", updated_at: "2026-11-14T22:31:00Z", change_1d: null, volume_24h: null }];
    state.datasets = { crypto };
    render(<><ThemesPage /><Footer /></>);
    expect(screen.getByTestId("coinmetrics-disclosure")).toHaveTextContent(disclosure);
    expect(screen.getByText(new RegExp(observed))).toBeInTheDocument();
    expect(within(screen.getByTestId("section-crypto")).getByTestId("asset-card")).toHaveTextContent("—");
    expect(within(screen.getByTestId("section-crypto")).getByText(locale === "en" ? "Stale" : "已过期")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CC BY-NC 4.0" })).toHaveAttribute("href", "https://creativecommons.org/licenses/by-nc/4.0/");
  },
);

it("does not attribute non-Coin-Metrics crypto data to Coin Metrics", async () => {
  await i18n.changeLanguage("en");
  state.datasets = { crypto: CryptoEnvelope.parse(FIXTURE_MAP["/latest/crypto.json"]) };
  render(<><ThemesPage /><Footer /></>);
  expect(screen.queryByTestId("coinmetrics-attribution")).not.toBeInTheDocument();
  expect(screen.queryByTestId("coinmetrics-disclosure")).not.toBeInTheDocument();
});

it("ages Coin Metrics source observations on the system status page", async () => {
  await i18n.changeLanguage("en");
  const now = new Date().toISOString();
  const oldSource = new Date(Date.now() - 60 * 60_000).toISOString();
  const crypto = CryptoEnvelope.parse(FIXTURE_MAP["/latest/crypto.json"]);
  crypto.provenance.provider = "coinmetrics";
  crypto.source_updated_at = oldSource;
  const freshness = structuredClone(FIXTURE_MAP["/metadata/freshness.json"]) as { datasets: Record<string, { status: string; updated_at: string }> };
  freshness.datasets.crypto.status = "fresh";
  freshness.datasets.crypto.updated_at = now;
  state.datasets = { crypto, freshness, sources: FIXTURE_MAP["/metadata/sources.json"], "schema-version": FIXTURE_MAP["/metadata/schema-version.json"] };
  render(<StatusPage />);
  const rows = screen.getAllByText("Crypto", { selector: "td" }).map((cell) => cell.closest("tr"));
  expect(rows.some((row) => row && within(row).queryByText("Stale"))).toBe(true);
});

it("shows cache replay and source age together on the system status page", async () => {
  await i18n.changeLanguage("en");
  const crypto = CryptoEnvelope.parse(FIXTURE_MAP["/latest/crypto.json"]);
  crypto.provenance.provider = "coinmetrics";
  crypto.provenance.from_cache = true;
  crypto.source_updated_at = new Date(Date.now() - 60 * 60_000).toISOString();
  const freshness = structuredClone(FIXTURE_MAP["/metadata/freshness.json"]) as { datasets: Record<string, { status: string; updated_at: string }> };
  freshness.datasets.crypto.status = "degraded";
  freshness.datasets.crypto.updated_at = new Date().toISOString();
  state.datasets = { crypto, freshness, sources: FIXTURE_MAP["/metadata/sources.json"], "schema-version": FIXTURE_MAP["/metadata/schema-version.json"] };
  render(<StatusPage />);
  const rows = screen.getAllByText("Crypto", { selector: "td" }).map((cell) => cell.closest("tr"));
  const row = rows.find((candidate) => candidate && within(candidate).queryByText("Cache replay"));
  expect(row).toBeTruthy();
  expect(within(row!).getByText("Source stale")).toBeInTheDocument();
});

it("does not call crypto fresh when its data and provider status are unavailable", async () => {
  await i18n.changeLanguage("en");
  const freshness = structuredClone(FIXTURE_MAP["/metadata/freshness.json"]) as { datasets: Record<string, { status: string; updated_at: string }> };
  freshness.datasets.crypto.status = "fresh";
  freshness.datasets.crypto.updated_at = new Date().toISOString();
  state.datasets = { freshness, "schema-version": FIXTURE_MAP["/metadata/schema-version.json"] };
  render(<StatusPage />);
  const row = screen.getByText("Crypto", { selector: "td" }).closest("tr");
  expect(row).toBeTruthy();
  expect(within(row!).getByText("Degraded")).toBeInTheDocument();
});
