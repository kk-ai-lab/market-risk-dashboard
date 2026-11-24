import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import i18n from "@/i18n";
import { RiskDimensionBreakdown } from "@/components/risk/RiskDimensionBreakdown";
import { RiskDimension, RiskEnvelope } from "@/schemas";
import { FIXTURE_MAP } from "./helpers/fetchMock";

afterEach(cleanup);

it("treats a missing legacy dimension trend as unknown", () => {
  const risk = RiskEnvelope.parse(FIXTURE_MAP["/latest/risk.json"]).payload;
  const { trend: _trend, ...legacy } = risk.dimensions[0];
  void _trend;
  expect(RiskDimension.parse(legacy).trend).toBe("unknown");
});

it("does not invent an evidence range and names missing inputs", async () => {
  await i18n.changeLanguage("en");
  const risk = RiskEnvelope.parse(FIXTURE_MAP["/latest/risk.json"]).payload;
  risk.score_lower_bound = null;
  risk.score_upper_bound = null;
  risk.dimensions[0].missing_indicators = ["real_rate_dfii10"];
  risk.dimensions[0].effective_coverage = null;
  render(<RiskDimensionBreakdown result={risk} />);
  expect(screen.getByTestId("risk-evidence-state")).toHaveTextContent("Evidence score range: —");
  const dimension = screen.getAllByTestId("risk-dimension")[0];
  expect(within(dimension).getByText(/Missing or excluded indicators:/)).toHaveTextContent("10Y Real Rate");
  expect(dimension).toHaveTextContent("Trust-adjusted coverage: —");
});

it("keeps implementation version numbers out of the risk summary", async () => {
  await i18n.changeLanguage("en");
  const risk = RiskEnvelope.parse(FIXTURE_MAP["/latest/risk.json"]).payload;
  render(<RiskDimensionBreakdown result={risk} />);
  expect(screen.queryByText(risk.model_version)).not.toBeInTheDocument();
  expect(screen.queryByText(risk.calibration_policy_version ?? "1.0.0")).not.toBeInTheDocument();
});
