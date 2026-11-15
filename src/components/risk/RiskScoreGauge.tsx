import { useTranslation } from "react-i18next";
import { TrendingDown, TrendingUp, Minus } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Progress } from "@/components/ui/Progress";
import { Badge } from "@/components/ui/Badge";
import { riskLevelTone, riskTrendTone, toneClasses } from "@/lib/riskColors";
import { RISK_LEVEL_KEYS } from "@/lib/riskLabels";
import { formatNumber, formatPctPoints, formatRatio } from "@/lib/format";
import type { RiskEvidenceState, RiskLevel } from "@/schemas";

/**
 * RiskScoreGauge: global risk score gauge (architecture §8.6: color + text + value).
 * Risk conclusion prioritized on the initial screen (PRD §22.3).
 */
export interface RiskScoreGaugeProps {
  score: number;
  level: RiskLevel;
  trend1d: number | null;
  trend1w: number | null;
  trend1m: number | null;
  confidence: number | null;
  hasEvidence: boolean;
  evidenceState: RiskEvidenceState | null;
}

export function RiskScoreGauge({ score, level, trend1d, trend1w, trend1m, confidence, hasEvidence, evidenceState }: RiskScoreGaugeProps) {
  const { t, i18n } = useTranslation("risk");
  const locale = i18n.language;
  const tone = hasEvidence ? riskLevelTone(level) : "na";
  const classes = toneClasses(tone);
  const trendTone = hasEvidence ? riskTrendTone(trend1d) : "na";
  const trendClasses = toneClasses(trendTone);

  const TrendIcon =
    !hasEvidence || trend1d === null || trend1d === undefined || trend1d === 0
      ? Minus
      : trend1d > 0
        ? TrendingUp
        : TrendingDown;

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>{t("score.title")}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-end justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <span className={`text-5xl font-bold tabular-nums ${classes.text}`} data-testid="risk-score">
              {hasEvidence ? formatNumber(score, locale) : t("common:data.na")}
            </span>
            <span className="text-xs text-muted-foreground">/ 100</span>
          </div>
          <Badge variant={tone} data-testid="risk-level">
            {hasEvidence ? t(RISK_LEVEL_KEYS[level]) : t(evidenceState === null ? "evidence.unknown" : "evidence.insufficient")}
          </Badge>
        </div>

        {hasEvidence ? <Progress value={score} barClassName={classes.bg} /> : null}

        <div className={`flex items-center gap-2 text-sm font-medium ${trendClasses.text}`}>
          <TrendIcon className="h-4 w-4" aria-hidden />
          <span data-testid="risk-trend">
            {!hasEvidence || trend1d === null || trend1d === undefined
              ? t("common:data.na")
              : `${t("common:direction.dayChange")} ${formatPctPoints(trend1d, locale)}`}
          </span>
        </div>

        <dl className="grid grid-cols-3 gap-2 text-center">
          {(
            [
              ["score.week", trend1w],
              ["score.month", trend1m],
              ["score.confidenceShort", confidence],
            ] as const
          ).map(([label, value]) => (
            <div key={label} className="rounded-md bg-muted/60 px-2 py-1.5">
              <dt className="text-xs uppercase tracking-wide text-muted-foreground">{t(label)}</dt>
              <dd className="text-sm font-semibold tabular-nums text-foreground">
                {(!hasEvidence && label !== "score.confidenceShort") || value === null || value === undefined
                  ? t("common:data.na")
                  : label === "score.confidenceShort"
                    ? formatRatio(value, locale)
                    : formatPctPoints(value, locale)}
              </dd>
            </div>
          ))}
        </dl>

        <p className="text-xs leading-relaxed text-muted-foreground">{t("score.disclaimer")}</p>
      </CardContent>
    </Card>
  );
}
