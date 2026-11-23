import type { RiskModelResult } from "@/schemas";

export function hasRiskEvidence(result: Pick<RiskModelResult, "dimensions" | "evidence_state" | "total_score" | "risk_level">): boolean {
  return result.evidence_state !== null && result.evidence_state !== "insufficient_evidence"
    && result.total_score !== null && result.risk_level !== null
    && result.dimensions.some((dimension) => dimension.indicators.some((indicator) => indicator.value !== null));
}
