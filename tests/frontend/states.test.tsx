/**
 * State component tests: ErrorState (JSON read failure) / StatusBadge (five states) / EmptyState.
 * Covers JSON read failure and stale/missing/degraded data states.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@/i18n";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/layout/StatusBadge";

describe("ErrorState (JSON read failure)", () => {
  it("renders title/message/retry and fires onRetry", () => {
    const onRetry = vi.fn();
    render(<ErrorState onRetry={onRetry} detail={["payload.total_score: invalid"]} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("数据加载失败")).toBeInTheDocument();
    expect(screen.getByText("payload.total_score: invalid")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("EmptyState (missing empty state)", () => {
  it("renders default title", () => {
    render(<EmptyState />);
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });
});

describe("StatusBadge (five states → badge)", () => {
  beforeEach(() => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("fresh → normal", () => {
    render(<StatusBadge status="fresh" />);
    expect(screen.getByTestId("status-badge-fresh")).toHaveTextContent("正常");
  });

  it("delayed → delayed (yellow hint)", () => {
    render(<StatusBadge status="delayed" />);
    expect(screen.getByTestId("status-badge-delayed")).toHaveTextContent("延迟");
  });

  it("stale → stale (prominent warning via fresh family, ADR-0002)", () => {
    render(<StatusBadge status="stale" />);
    const badge = screen.getByTestId("status-badge-stale");
    expect(badge).toHaveTextContent("已过期");
    expect(badge.className).toContain("fresh-warn");
  });

  it("missing → no data", () => {
    render(<StatusBadge status="missing" />);
    expect(screen.getByTestId("status-badge-missing")).toHaveTextContent("无数据");
  });

  it("cache replay badge is distinct from delayed (#66)", () => {
    render(<StatusBadge status="delayed" fromCache />);
    const badge = screen.getByTestId("status-badge-cache");
    expect(badge).toHaveTextContent("缓存回放");
    expect(badge).not.toHaveTextContent("延迟");
  });

  it("degraded → partially degraded", () => {
    render(<StatusBadge status="degraded" withDescription />);
    expect(screen.getByTestId("status-badge-degraded")).toHaveTextContent("部分降级");
    expect(screen.getByText(/数据源降级/)).toBeInTheDocument();
  });
});
