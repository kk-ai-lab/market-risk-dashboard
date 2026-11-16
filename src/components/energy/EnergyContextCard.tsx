import { useTranslation } from "react-i18next";
import type { EnergyEnvelope } from "@/schemas";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatDate, formatDateTime, formatNumber, formatSignedNumber } from "@/lib/format";

const EIA_SERIES_URL = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?f=W&n=PET&s=WCESTUS1";
const OBSERVATION_WINDOW_DAYS = 14;

function currentObservationAge(weekEnding?: string): "unknown" | "within_window" | "overdue" {
  if (!weekEnding) return "unknown";
  const [year, month, day] = weekEnding.split("-").map(Number);
  if (!year || !month || !day) return "unknown";
  const observedAt = Date.UTC(year, month - 1, day);
  const today = new Date();
  const todayAt = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  const ageDays = Math.floor((todayAt - observedAt) / 86_400_000);
  if (ageDays < 0) return "unknown";
  return ageDays <= OBSERVATION_WINDOW_DAYS ? "within_window" : "overdue";
}

export interface EnergyContextCardProps {
  dataset?: EnergyEnvelope;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  contextOnly?: boolean;
}

/** Weekly EIA inventory context. The displayed age advances from the reported period end. */
export function EnergyContextCard({
  dataset,
  isLoading = false,
  isError = false,
  onRetry,
  contextOnly = false,
}: EnergyContextCardProps) {
  const { t, i18n } = useTranslation("themes");
  const locale = i18n.language;
  const payload = dataset?.payload;
  const observations = payload?.observations ?? [];
  const latest = observations[observations.length - 1];
  const previous = observations[observations.length - 2];
  const weeklyChange = latest && previous ? latest.value - previous.value : null;
  const collectionStatus = payload?.collection_status ?? "not_configured";
  const age = currentObservationAge(latest?.week_ending);
  const collectionTone = collectionStatus === "available" ? "freshOk" : "freshWarn";
  const ageTone = age === "within_window" ? "freshOk" : age === "overdue" ? "freshWarn" : "outline";

  return (
    <Card data-testid="energy-context-card">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle>{t(contextOnly ? "energy.riskContextTitle" : "energy.title")}</CardTitle>
          <CardDescription>{t("energy.description")}</CardDescription>
        </div>
        {payload ? (
          <div className="flex flex-wrap gap-1.5" aria-label={t("energy.statusLabel")}>
            <Badge variant={collectionTone}>{t(`energy.collection.${collectionStatus}`)}</Badge>
            <Badge variant={ageTone}>{t(`energy.age.${age}`)}</Badge>
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {contextOnly ? <p className="text-xs text-muted-foreground">{t("energy.riskContextNote")}</p> : null}
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : isError ? (
          <ErrorState onRetry={onRetry} />
        ) : payload ? (
          <>
            {latest ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label={t("energy.level")} value={`${formatNumber(latest.value, locale, 1)} ${t("energy.unit")}`} />
                <Metric
                  label={t("energy.weeklyChange")}
                  value={weeklyChange === null ? t("common:data.na") : `${formatSignedNumber(weeklyChange, locale, 1)} ${t("energy.unit")}`}
                />
                <Metric label={t("energy.weekEnding")} value={formatDate(`${latest.week_ending}T12:00:00`, locale)} />
                <Metric
                  label={t("energy.lastRetrieved")}
                  value={payload.last_successful_fetch_at ? formatDateTime(payload.last_successful_fetch_at, locale) : t("common:data.na")}
                />
              </div>
            ) : (
              <EmptyState
                title={t(collectionStatus === "empty" ? "energy.noObservations" : "energy.waitingForData")}
                message={t("energy.checkTime", { time: formatDateTime(payload.last_attempt_at, locale) })}
              />
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-hairline pt-2 text-xs text-muted-foreground">
              {!latest ? (
                <span>
                  {t("energy.lastRetrieved")}: {payload.last_successful_fetch_at
                    ? formatDateTime(payload.last_successful_fetch_at, locale)
                    : t("common:data.na")}
                </span>
              ) : null}
              {latest ? (
                <span>{t("energy.lastCheck", { time: formatDateTime(payload.last_attempt_at, locale) })}</span>
              ) : null}
              <span>{t("energy.ageRule", { days: OBSERVATION_WINDOW_DAYS })}</span>
              <a
                href={EIA_SERIES_URL}
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2 hover:text-foreground"
              >
                {t("energy.attribution")}
              </a>
            </div>
          </>
        ) : (
          <EmptyState title={t("energy.waitingForData")} />
        )}
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded border border-hairline bg-muted/20 p-2">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm font-semibold tabular-nums text-foreground">{value}</span>
    </div>
  );
}
