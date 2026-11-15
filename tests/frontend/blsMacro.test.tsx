import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { MacroIndicatorCard } from "@/components/macro/MacroIndicatorCard";
import { MacroIndicator } from "@/schemas/generated/contracts";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("BLS macro observation", () => {
  it.each([["zh-CN", "观测月份", "采集时间"], ["en", "Observation month", "Fetched"]])(
    "distinguishes observation month and fetch time in %s",
    async (locale, monthLabel, fetchLabel) => {
      await i18n.changeLanguage(locale);
      const indicator = MacroIndicator.parse({ key: "cpiaucsl", label: "CPI", value: 2.4, source: "BLS", unit: "pct", status: "fresh", updated_at: null, observation_period: "2026-10", fetched_at: "2026-11-14T00:00:00Z" });
      render(<MacroIndicatorCard indicator={indicator} />);
      expect(screen.getByText(new RegExp(monthLabel))).toHaveTextContent("2026-10");
      expect(screen.getByText(new RegExp(fetchLabel))).toBeInTheDocument();
      expect(screen.getByText(/BLS/)).toBeInTheDocument();
    },
  );

  it("ages the badge from the observation date while fetched time remains recent", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-15T00:15:00Z"));
    await i18n.changeLanguage("en");
    const indicator = MacroIndicator.parse({
      key: "dgs10", label: "10Y yield", value: 4.5, source: "Treasury", unit: "pct",
      status: "fresh", updated_at: null, observation_date: "2026-11-15",
      fetched_at: "2026-11-15T00:14:00Z",
      freshness_policy: { delayed_after_minutes: 10, stale_after_minutes: 20 },
    });
    render(<MacroIndicatorCard indicator={indicator} />);

    expect(screen.getByTestId("status-badge-delayed")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(6 * 60_000));
    expect(screen.getByTestId("status-badge-stale")).toBeInTheDocument();
  });

  it("ages monthly observations from period end instead of month start", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-15T00:15:00Z"));
    await i18n.changeLanguage("en");
    const indicator = MacroIndicator.parse({
      key: "cpiaucsl", label: "CPI", value: 2.4, source: "BLS", unit: "pct",
      status: "fresh", observation_period: "2026-10", fetched_at: "2026-11-14T00:00:00Z",
      freshness_policy: { delayed_after_minutes: 54_000, stale_after_minutes: 108_000 },
    });
    render(<MacroIndicatorCard indicator={indicator} />);

    expect(screen.getByTestId("status-badge-fresh")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(31 * 24 * 60 * 60_000));
    expect(screen.getByTestId("status-badge-delayed")).toBeInTheDocument();
  });

  it("labels legacy records whose freshness policy is unavailable", async () => {
    await i18n.changeLanguage("en");
    const indicator = MacroIndicator.parse({
      key: "dgs10", label: "10Y yield", value: 4.5, source: "Treasury", unit: "pct",
      status: "fresh", observation_date: "2026-11-15",
    });
    render(<MacroIndicatorCard indicator={indicator} />);

    expect(screen.getByTestId("status-badge-degraded")).toBeInTheDocument();
    expect(screen.getByText("Source freshness unavailable for this legacy record")).toBeInTheDocument();
  });

  it.each([["zh-CN", "观测日期", "采集时间"], ["en", "Observation date", "Fetched"]])(
    "distinguishes Treasury observation date and fetch time in %s",
    async (locale, dateLabel, fetchLabel) => {
      await i18n.changeLanguage(locale);
      const indicator = MacroIndicator.parse({ key: "dgs10", label: "10Y yield", value: 4.5, source: "Treasury", unit: "pct", status: "fresh", updated_at: null, observation_date: "2026-11-13", fetched_at: "2026-11-15T00:00:00Z" });
      render(<MacroIndicatorCard indicator={indicator} />);
      expect(screen.getByText(new RegExp(dateLabel))).toHaveTextContent("2026-11-13");
      expect(screen.getByText(new RegExp(fetchLabel))).toBeInTheDocument();
      expect(screen.getByText(locale === "zh-CN" ? /Treasury/ : /U\.S\. Treasury/)).toBeInTheDocument();
    },
  );
});
