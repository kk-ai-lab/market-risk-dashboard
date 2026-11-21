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

const CRYPTO_NAMES = [
  ["BTC", "Bitcoin", "比特币"],
  ["ETH", "Ethereum", "以太坊"],
  ["SOL", "Solana", "索拉纳"],
  ["ADA", "Cardano", "卡尔达诺"],
  ["XRP", "XRP", "瑞波币"],
  ["DOGE", "Dogecoin", "狗狗币"],
  ["BNB", "BNB", "币安币"],
  ["LINK", "Chainlink", "Chainlink 代币"],
  ["LTC", "Litecoin", "莱特币"],
  ["AVAX", "Avalanche", "雪崩"],
] as const;

it.each([
  [
    "en",
    "Source observation",
    "BTC, ETH, SOL, ADA, XRP, DOGE, BNB, LINK, LTC, and AVAX are Coin Metrics market-wide reference rates, not exchange last trades",
    "This source does not provide 24-hour changes",
  ],
  [
    "zh-CN",
    "来源观测时间",
    "所展示的加密资产价格为全市场参考价，并非单一交易所的最新成交价",
    "该来源不提供 24 小时涨跌幅",
  ],
])(
  "shows ten reference assets, source timing, limits and the license in %s",
  async (locale, observed, rateDescription, changeDescription) => {
    await i18n.changeLanguage(locale);
    const crypto = CryptoEnvelope.parse(FIXTURE_MAP["/latest/crypto.json"]);
    crypto.freshness_status = "stale";
    crypto.payload.assets = CRYPTO_NAMES.map(([symbol, name]) => ({
      ...crypto.payload.assets[0],
      symbol,
      name,
      price: 100,
      source: "coinmetrics",
      updated_at: "2026-11-14T22:31:00Z",
      change_1d: null,
      volume_24h: null,
    }));
    state.datasets = { crypto };
    render(<><ThemesPage /><Footer /></>);
    const section = within(screen.getByTestId("section-crypto"));
    const disclosure = screen.getByTestId("coinmetrics-disclosure");
    expect(disclosure).toHaveTextContent(rateDescription);
    expect(disclosure).toHaveTextContent(changeDescription);
    expect(screen.getAllByText(new RegExp(observed))).toHaveLength(10);
    expect(section.getAllByTestId("asset-card")).toHaveLength(10);
    for (const card of section.getAllByTestId("asset-card")) {
      expect(card).toHaveTextContent("—");
    }
    for (const [symbol, english, chinese] of CRYPTO_NAMES) {
      expect(section.getByText(locale === "en" ? english : chinese, { selector: "p" })).toBeInTheDocument();
      expect(section.getByText(symbol, { selector: "span" })).toBeInTheDocument();
    }
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
