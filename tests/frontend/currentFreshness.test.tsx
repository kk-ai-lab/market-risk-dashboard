import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StatusBadge } from "@/components/layout/StatusBadge";
import { currentFreshness } from "@/lib/freshness";

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("published data freshness", () => {
  it("ages a run without upgrading published warnings or clearing provider failures", () => {
    const generated = "2026-01-01T00:00:00Z";
    const start = Date.parse(generated);
    expect(currentFreshness("fresh", "risk", generated, start + 12 * 60 * 60_000)).toBe("fresh");
    expect(currentFreshness("fresh", "risk", generated, start + 13 * 60 * 60_000)).toBe("delayed");
    expect(currentFreshness("fresh", "risk", generated, start + 25 * 60 * 60_000)).toBe("stale");
    expect(currentFreshness("stale", "risk", generated, start)).toBe("stale");
    expect(currentFreshness("degraded", "risk", generated, start + 25 * 60 * 60_000)).toBe("degraded");
    expect(currentFreshness("fresh", "risk", "invalid", start)).toBe("stale");
    expect(currentFreshness("fresh", "risk", generated, start - 6 * 60_000)).toBe("stale");
  });

  it("updates a badge while the page stays open", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T12:00:00Z"));
    render(<StatusBadge status="fresh" dataset="risk" generatedAt="2026-01-01T00:00:00Z" />);
    expect(screen.getByTestId("status-badge-fresh")).toBeTruthy();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByTestId("status-badge-delayed")).toBeTruthy();
  });

  it.each(["degraded", "empty"] as const)("shows old %s data and its age together", (status) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-02T02:00:00Z"));
    render(<StatusBadge status={status} dataset="risk" generatedAt="2026-01-01T00:00:00Z" />);
    expect(screen.getByTestId(`status-badge-${status}`)).toBeTruthy();
    expect(screen.getByTestId("status-badge-age-stale")).toBeTruthy();
  });

  it("shows age alongside cache replay", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-02T02:00:00Z"));
    render(<StatusBadge status="degraded" fromCache dataset="risk" generatedAt="2026-01-01T00:00:00Z" />);
    expect(screen.getByTestId("status-badge-cache")).toBeTruthy();
    expect(screen.getByTestId("status-badge-age-stale")).toBeTruthy();
  });

  it("ages the underlying source independently from the snapshot", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:22:00Z"));
    render(<StatusBadge status="fresh" dataset="crypto" generatedAt="2026-01-01T00:22:00Z" sourceUpdatedAt="2026-01-01T00:00:00Z" sourceExpectedMinutes={15} />);
    expect(screen.getByTestId("status-badge-fresh")).toBeTruthy();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByTestId("status-badge-delayed")).toBeTruthy();
    act(() => vi.advanceTimersByTime(23 * 60_000));
    expect(screen.getByTestId("status-badge-stale")).toBeTruthy();
  });

  it("shows source age beside a cache replay", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T01:00:00Z"));
    render(<StatusBadge status="degraded" fromCache dataset="crypto" generatedAt="2026-01-01T00:59:00Z" sourceUpdatedAt="2026-01-01T00:00:00Z" sourceExpectedMinutes={15} />);
    expect(screen.getByTestId("status-badge-cache")).toBeTruthy();
    expect(screen.getByTestId("status-badge-source-stale")).toBeTruthy();
  });

  it("uses the source interval for an old snapshot even if its source time is newer", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T01:00:00Z"));
    render(<StatusBadge status="fresh" dataset="crypto" generatedAt="2026-01-01T00:00:00Z" sourceUpdatedAt="2026-01-01T01:00:00Z" sourceExpectedMinutes={15} />);
    expect(screen.getByTestId("status-badge-stale")).toBeTruthy();
  });
});
