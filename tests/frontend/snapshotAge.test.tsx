import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { SnapshotTimestamp } from "@/components/dashboard/SnapshotTimestamp";
import { snapshotAge } from "@/lib/snapshotAge";

const now = Date.parse("2026-11-13T12:20:00Z");

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("snapshot time", () => {
  it("measures artifact age from generated_at, including sub-minute ages", () => {
    expect(snapshotAge({ generatedAt: "2026-11-13T12:05:00Z", now })).toEqual({
      kind: "known", generatedAt: "2026-11-13T12:05:00Z", ageMinutes: 15,
    });
    expect(snapshotAge({ generatedAt: "2026-11-13T12:19:30Z", now })).toMatchObject({ ageMinutes: 0 });
  });

  it.each([undefined, null, "", "invalid", "2026-11-13 12:05:00", "2026-02-30T12:05:00Z", "2026-11-13T12:21:00Z"])(
    "treats missing, invalid, and future timestamps as unknown (%s)",
    (generatedAt) => expect(snapshotAge({ generatedAt, now })).toEqual({ kind: "unknown" }),
  );

  it("does not call a non-finite browser clock a known age", () => {
    expect(snapshotAge({ generatedAt: "2026-11-13T12:05:00Z", now: NaN })).toEqual({ kind: "unknown" });
  });

  it("updates visible age each minute without upgrading source freshness", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    render(<SnapshotTimestamp generatedAt="2026-11-13T12:05:00Z" />);
    expect(screen.getByTestId("snapshot-age")).toHaveTextContent("15 分钟前");
    expect(screen.getByTestId("snapshot-timestamp")).toHaveTextContent("并非交易所行情观测时间");
    expect(screen.getByTestId("snapshot-timestamp")).toHaveTextContent("来源数据可能更早");
    expect(screen.getByRole("time")).toHaveAttribute("datetime", "2026-11-13T12:05:00Z");

    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByTestId("snapshot-age")).toHaveTextContent("16 分钟前");
    expect(screen.getByTestId("snapshot-timestamp")).not.toHaveTextContent("正常");
  });

  it("shows unknown for missing times in both languages", async () => {
    const { rerender } = render(<SnapshotTimestamp generatedAt={undefined} />);
    expect(screen.getByTestId("snapshot-age")).toHaveTextContent("未知");
    await act(async () => { await i18n.changeLanguage("en"); });
    rerender(<SnapshotTimestamp generatedAt="bad date" />);
    expect(screen.getByTestId("snapshot-timestamp")).toHaveTextContent("Snapshot generated: Unknown");
    expect(screen.getByTestId("snapshot-age")).toHaveTextContent("Snapshot age: Unknown");
    expect(screen.getByTestId("snapshot-timestamp")).toHaveTextContent("not an exchange observation time");
  });

  it("uses the current clock when a newer snapshot arrives between minute ticks", () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const { rerender } = render(<SnapshotTimestamp generatedAt="2026-11-13T12:05:00Z" />);
    vi.setSystemTime(now + 30_000);
    rerender(<SnapshotTimestamp generatedAt="2026-11-13T12:20:20Z" />);
    expect(screen.getByTestId("snapshot-age")).toHaveTextContent("0 分钟前");
  });

  it("recomputes elapsed time when the browser resumes", () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    render(<SnapshotTimestamp generatedAt="2026-11-13T12:05:00Z" />);
    vi.setSystemTime(now + 60 * 60_000);
    act(() => { window.dispatchEvent(new Event("focus")); });
    expect(screen.getByTestId("snapshot-age")).toHaveTextContent("75 分钟前");
  });
});
