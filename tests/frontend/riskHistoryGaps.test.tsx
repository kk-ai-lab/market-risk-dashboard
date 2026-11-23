import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import "@/i18n";
import { RiskTrendChart } from "@/charts/RiskTrendChart";

afterEach(cleanup);

describe("risk history gaps", () => {
  it("shows a missing entry between observations separated by days", async () => {
    render(<RiskTrendChart points={[
      { date: "2026-01-01", total_score: 40 },
      { date: "2026-01-04", total_score: 60 },
    ]} />);
    const table = await screen.findByTestId("trend-fallback");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[2]).toHaveTextContent("—");
    expect(rows[1]).toHaveTextContent("40");
    expect(rows[3]).toHaveTextContent("60");
  });
});
