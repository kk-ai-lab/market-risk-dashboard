import type { RiskModelResult } from "@/schemas";

export function hasRiskEvidence(result: Pick<RiskModelResult, "dimensions" | "evidence_state">): boolean {
  return result.evidence_state !== null && result.evidence_state !== "insufficient_evidence"
    && result.dimensions.some((dimension) => dimension.indicators.some((indicator) => indicator.value !== null));
}
