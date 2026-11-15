import { describe, expect, it } from "vitest";
import { riskLevelForScore } from "@/lib/riskColors";

describe("risk score bands", () => {
  it.each([
    [0, "risk_on"], [20, "low_risk"], [40, "caution"],
    [60, "high_risk"], [80, "severe_risk"], [90, "crisis"],
  ] as const)("uses the configured lower boundary at %s", (score, level) => {
    expect(riskLevelForScore(score)).toBe(level);
  });
});
