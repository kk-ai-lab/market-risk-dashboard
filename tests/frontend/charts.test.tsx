/**
 * Chart empty-state tests (architecture §8.8 missing → EmptyState) + jsdom degraded rendering.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import "@/i18n";
import { RiskTrendChart } from "@/charts/RiskTrendChart";
import { MarketSnapshot } from "@/charts/MarketSnapshot";
import { MacroChart } from "@/charts/MacroChart";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RiskTrendChart empty state", () => {
  it("points=[] → EmptyState (chart-empty)", () => {
    render(<RiskTrendChart points={[]} />);
    expect(screen.getByTestId("chart-empty")).toBeInTheDocument();
  });

  it("non-empty points → renders chart or HTML fallback (jsdom has no canvas)", () => {
    render(
      <RiskTrendChart
        points={[
          { date: "2026-08-01", total_score: 48.1 },
          { date: "2026-08-02", total_score: 52.3 },
        ]}
      />,
    );
    const chart = document.querySelector('[data-testid="risk-trend-chart"], [data-testid="trend-fallback"]');
    expect(chart).not.toBeNull();
  });
});

describe("MarketSnapshot states", () => {
  it("items=[] explains the empty snapshot", () => {
    render(<MarketSnapshot items={[]} />);
    expect(screen.getByTestId("market-snapshot-empty")).toBeInTheDocument();
  });

  it("groups dated observations by asset class and shows levels with units", () => {
    render(
      <MarketSnapshot
        items={[
          { symbol: "OECD:USA", label: "United States broad index", label_zh: "美国综合指数", category: "equity_index", value: 225.03, unit: "index, 2015=100", change_percent: 1.2, change_period: "1m", observation_date: null, observation_period: "2026-10", frequency: "monthly", source: "OECD", source_url: null, attribution: null },
          { symbol: "BTC", label: "Bitcoin", label_zh: null, category: "crypto", value: 90000, unit: "USD", change_percent: -0.8, change_period: "24h", observation_date: "2026-11-24", observation_period: null, frequency: "daily", source: "Coin Metrics", source_url: null, attribution: null },
          { symbol: "WB:BRENT", label: "Brent Crude", label_zh: "布伦特原油", category: "commodity", value: 72.5, unit: "$/bbl", change_percent: null, change_period: null, observation_date: null, observation_period: "2026-10", frequency: "monthly", source: "World Bank Pink Sheet", source_url: null, attribution: null },
        ]}
      />,
    );
    expect(screen.getByTestId("market-snapshot")).toBeInTheDocument();
    expect(screen.getAllByTestId("market-snapshot-category")).toHaveLength(3);
    expect(screen.getAllByTestId("market-snapshot-item")).toHaveLength(3);
    expect(screen.getByText("$90,000")).toBeInTheDocument();
    expect(screen.getAllByText(/2026-10/)).toHaveLength(2);
  });
});

describe("MacroChart empty state", () => {
  it("items=[] → EmptyState", () => {
    render(<MacroChart items={[]} />);
    expect(screen.getByTestId("chart-empty")).toBeInTheDocument();
  });

  it("non-empty items → renders chart or HTML fallback", () => {
    render(
      <MacroChart
        items={[
          { label: "10Y Yield", value: 4.68, unit: "%" },
          { label: "VIX", value: 15.99 },
        ]}
      />,
    );
    const chart = document.querySelector('[data-testid="macro-chart"], [data-testid="macro-chart-fallback"]');
    expect(chart).not.toBeNull();
  });
});
