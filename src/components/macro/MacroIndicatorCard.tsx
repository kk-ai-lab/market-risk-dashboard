import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { StatusBadge } from "@/components/layout/StatusBadge";
import { formatDateTime, formatNumber, formatUnitSuffix } from "@/lib/format";
import { macroPeriodEndAwareTimestamp } from "@/lib/freshness";
import type { MacroIndicator } from "@/schemas";

/**
 * MacroIndicatorCard: macro indicator card (value + unit + MoM change + freshness).
 */
export interface MacroIndicatorCardProps {
  indicator: MacroIndicator;
}

export function MacroIndicatorCard({ indicator }: MacroIndicatorCardProps) {
  const { t, i18n } = useTranslation("macro");
  const locale = i18n.language;
  const sourceTranslationKey = indicator.source === "U.S. Department of Labor"
    ? "departmentOfLabor"
    : indicator.source;
  const sourceLabel = t(`sources.${sourceTranslationKey}`, { defaultValue: indicator.source });
  const sourceTermsUrl: Record<string, string> = {
    "New York Fed": "https://www.newyorkfed.org/privacy/termsofuse",
    "Federal Reserve Board": "https://www.federalreserve.gov/disclaimer.htm",
    "Bureau of Economic Analysis": "https://www.bea.gov/open-data",
    "U.S. Department of Labor": "https://oui.doleta.gov/unemploy/claims.asp",
  };
  const observationAt = macroPeriodEndAwareTimestamp(
    indicator.observation_date,
    indicator.observation_period,
    indicator.updated_at,
  );
  const timeBasedStatus = ["fresh", "delayed", "stale"].some((status) => status === indicator.status);
  const badgeStatus = !indicator.freshness_policy && timeBasedStatus ? "degraded" : indicator.status;

  return (
    <Card className="h-full" data-testid="macro-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs">{t(`indicatorNames.${indicator.key}`, { defaultValue: t("indicatorNames.unknown") })}</CardTitle>
        <StatusBadge
          status={badgeStatus}
          observationAt={observationAt}
          observationPolicy={indicator.freshness_policy}
        />
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <p className="text-2xl font-bold tabular-nums">
          {indicator.value === null ? t("common:data.na") : formatNumber(indicator.value, locale)}
          {indicator.value !== null ? <span className="ml-1 text-xs font-normal text-muted-foreground">{formatUnitSuffix(indicator.unit, locale)}</span> : null}
        </p>
        <p className="text-xs text-muted-foreground">
          {indicator.change_1m === null || indicator.change_1m === undefined
            ? t("common:data.na")
            : `${t("indicator.change1m")} ${indicator.change_1m > 0 ? "+" : ""}${formatNumber(indicator.change_1m, locale)}${formatUnitSuffix(indicator.unit, locale)}`}
        </p>
        {indicator.updated_at ? (
          <p className="text-xs tabular-nums text-muted-foreground">{formatDateTime(indicator.updated_at, locale)}</p>
        ) : null}
        {indicator.observation_period ? <p className="text-xs tabular-nums text-muted-foreground">{t("indicator.observationPeriod")}: {indicator.observation_period} · {sourceLabel}</p> : null}
        {indicator.availability_reason === "latest_yoy_period_unavailable" && indicator.availability_period ? (
          <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="macro-availability-reason">
            {t("indicator.latestYoyPeriodUnavailable", {
              period: indicator.availability_period,
              observed: indicator.observation_period ?? indicator.observation_date?.slice(0, 7) ?? "—",
            })}
          </p>
        ) : null}
        {indicator.observation_date ? <p className="text-xs tabular-nums text-muted-foreground">{t("indicator.observationDate")}: {indicator.observation_date} · {sourceLabel}</p> : null}
        {indicator.fetched_at ? <p className="text-xs tabular-nums text-muted-foreground">{t("indicator.fetchedAt")}: {formatDateTime(indicator.fetched_at, locale)}</p> : null}
        {indicator.attribution ? (
          <p className="text-xs text-muted-foreground">
            {indicator.attribution}{sourceTermsUrl[indicator.source] ? <> <a className="underline underline-offset-2 hover:text-foreground" href={sourceTermsUrl[indicator.source]} target="_blank" rel="noreferrer">{t("indicator.terms")}</a></> : null}
          </p>
        ) : null}
        {!indicator.freshness_policy && timeBasedStatus ? <p className="text-xs text-muted-foreground">{t("indicator.freshnessUnavailable")}</p> : null}
      </CardContent>
    </Card>
  );
}
