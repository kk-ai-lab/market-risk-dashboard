import { useTranslation } from "react-i18next";
import { ArrowUpRight, CalendarClock, Info, Layers, Minus, TrendingDown, TrendingUp } from "lucide-react";
import { useDataset } from "@/hooks/useDataset";
import { hasRiskEvidence } from "@/lib/riskEvidence";
import { isOfficialAnnouncement } from "@/lib/officialAnnouncements";
import { SnapshotTimestamp } from "@/components/dashboard/SnapshotTimestamp";
import { RiskTrendSlice } from "@/schemas/history";
import type { DashboardEnvelope, MacroEnvelope, NewsEnvelope } from "@/schemas";
import { AssetHeatmap } from "@/charts/AssetHeatmap";
import { RiskTrendChart } from "@/charts/RiskTrendChart";
import { NewsCard } from "@/components/news/NewsCard";
import { EventCard } from "@/components/calendar/EventCard";
import { KpiCard } from "@/components/ui/KpiCard";
import { Badge } from "@/components/ui/Badge";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusBadge } from "@/components/layout/StatusBadge";
import { CoinMetricsAttribution } from "@/components/crypto/CoinMetricsAttribution";
import { dirTone, dirClasses, regimeTone, riskLevelTone, riskTrendTone, toneClasses } from "@/lib/riskColors";
import { RISK_DIMENSION_KEYS, RISK_EVIDENCE_KEYS, RISK_INDICATOR_KEYS, RISK_LEVEL_KEYS, regimeKey } from "@/lib/riskLabels";
import { formatChange, formatNumber, formatPctPoints, formatRatio } from "@/lib/format";
import type { RiskDimensionKey } from "@/schemas";
import type { HeatmapCell } from "@/charts/AssetHeatmap";

/**
 * OverviewPage: dark-first terminal layout (spec #23 ticket #28 — reference implementation).
 * - Risk KPI strip plus a separate credit-indicator row
 * - Charts live in open chart regions (surface-0 + section header + hairline, no card chrome)
 * - Drivers/catalysts/sectors/news are hairline-divided sections with tabular numerals
 * - Page footer: mono status line (generation time + freshness)
 */
export default function OverviewPage() {
  const { t, i18n } = useTranslation("dashboard");
  const locale = i18n.language;

  const dashboardQ = useDataset<DashboardEnvelope>("dashboard");
  const trendQ = useDataset<RiskTrendSlice>("risk", { slice: "30d" }, RiskTrendSlice);
  const macroQ = useDataset<MacroEnvelope>("macro");
  const newsQ = useDataset<NewsEnvelope>("news");
  const dashboard = dashboardQ.data?.payload;
  const risk = dashboard?.risk;

  // The dashboard artifact is the homepage read model: its producer already selects
  // the first-view assets, catalysts, drivers, and sector rows. Keep labels localized
  // while avoiding browser-side reconstruction from the source datasets.
  const categoryLabels: Record<string, string> = {
    equity: t("heatmap.catEquities"),
    crypto: t("heatmap.catCrypto"),
    sector: t("heatmap.catSectors"),
  };
  const heatmapCells: HeatmapCell[] =
    dashboard?.cross_asset.map((asset) => ({
      asset: asset.asset,
      category: categoryLabels[asset.category] ?? asset.category,
      change1d: asset.change_1d,
    })) ?? [];
  const catalysts = dashboard?.catalysts ?? [];
  const sectorBaskets = dashboard?.sector_performance ?? [];

  const topNews = newsQ.data
    ? newsQ.data.payload.items.filter(isOfficialAnnouncement).sort((a, b) => b.importance - a.importance).slice(0, 4)
    : [];

  const riskAvailable = risk ? hasRiskEvidence(risk) : false;
  const riskTone = risk && riskAvailable ? riskLevelTone(risk.risk_level) : "na";
  const trendTone = risk && riskAvailable ? riskTrendTone(risk.trend_1d) : "na";
  const TrendIcon =
    !riskAvailable || risk?.trend_1d === null || risk?.trend_1d === undefined || risk.trend_1d === 0
      ? Minus
      : risk.trend_1d > 0
        ? TrendingUp
        : TrendingDown;
  const topDrivers = dashboard?.top_drivers ?? [];

  const creditIndicators = macroQ.data?.payload.credit ?? [];
  const baaSpread = creditIndicators.find((indicator) => indicator.key === "baa10y") ?? null;
  const hyOas = creditIndicators.find((indicator) => indicator.key === "bamlh0a0hym2") ?? null;
  const igOas = creditIndicators.find((indicator) => indicator.key === "bamlc0a0cm") ?? null;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-lg font-semibold text-foreground" data-testid="page-title">
          {t("title")}
        </h1>
        <p className="text-xs text-muted-foreground">{t("subtitle")}</p>
        {dashboardQ.data ? (
          <span className="ml-auto flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">{t("snapshot.recordedStatus")}</span>
            <StatusBadge dataset="dashboard" generatedAt={dashboardQ.data.generated_at} status={dashboardQ.data.freshness_status} fromCache={dashboardQ.data.provenance?.from_cache} withDescription />
          </span>
        ) : null}
      </header>

      <SnapshotTimestamp generatedAt={dashboardQ.data?.generated_at} />

      {/* KPI strip: the only cards on this page (card policy) */}
      <section className="grid grid-cols-1 gap-3 md:grid-cols-3" data-testid="risk-conclusion">
        {dashboardQ.isLoading ? (
          <>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </>
        ) : dashboardQ.isError ? (
          <ErrorState onRetry={dashboardQ.refetch} className="md:col-span-3" />
        ) : risk && dashboardQ.data ? (
          <>
            <KpiCard
              label={t("risk:score.title")}
              footer={
                <span className={`inline-flex items-center gap-1 ${toneClasses(trendTone).text}`}>
                  <TrendIcon className="h-3 w-3" aria-hidden />
                  {!riskAvailable || risk.trend_1d === null
                    ? t("common:data.na")
                    : `${t("common:direction.dayChange")} ${formatPctPoints(risk.trend_1d, locale)}`}
                </span>
              }
            >
              <span className={`text-2xl font-semibold tabular-nums ${toneClasses(riskTone).text}`} data-testid="risk-score">
                {riskAvailable ? formatNumber(risk.total_score, locale) : t("common:data.na")}
              </span>
              <Badge variant={riskTone} data-testid="risk-level">
                {riskAvailable ? t(`risk:${RISK_LEVEL_KEYS[risk.risk_level]}`) : t(risk.evidence_state === null ? "risk:evidence.unknown" : "risk:evidence.insufficient")}
              </Badge>
            </KpiCard>

            <KpiCard
              label={t("risk:regime.title")}
              footer={`${t("risk:score.confidence")} ${formatRatio(risk.confidence, locale)}`}
            >
              <span
                className={`text-base font-medium ${toneClasses(riskAvailable ? regimeTone(risk.regime) : "na").text}`}
                data-testid="market-regime"
              >
                {riskAvailable ? t(`risk:${regimeKey(risk.regime)}`) : t("risk:regime.noEvidence")}
              </span>
            </KpiCard>

            <KpiCard
              label={t("kpi.trend1d")}
              footer={
                !riskAvailable || risk.trend_1w === null
                  ? undefined
                  : `${t("kpi.oneWeek")} ${formatPctPoints(risk.trend_1w, locale)} · ${t("kpi.oneMonth")} ${risk.trend_1m === null ? t("common:data.na") : formatPctPoints(risk.trend_1m, locale)}`
              }
            >
              <span className={`text-2xl font-semibold tabular-nums ${toneClasses(trendTone).text}`}>
                {!riskAvailable || risk.trend_1d === null ? t("common:data.na") : formatPctPoints(risk.trend_1d, locale)}
              </span>
            </KpiCard>

          </>
        ) : (
          <EmptyState className="md:col-span-3" />
        )}
      </section>

      <section className="grid grid-cols-1 gap-3 md:grid-cols-3" data-testid="credit-indicators">
        <KpiCard
          label={t("kpi.baaSpread")}
          footer={
            <span className="flex flex-wrap items-center gap-x-1">
              <span>{baaSpread?.attribution ?? t("kpi.baaAttribution")}</span>
              <span>· {t("kpi.baaSpreadNote")}</span>
              {baaSpread?.observation_date ? <span>· {t("kpi.observed")} {baaSpread.observation_date}</span> : null}
            </span>
          }
          data-testid="credit-baa10y"
        >
          {baaSpread?.value !== null && baaSpread?.value !== undefined ? (
            <span className={`text-2xl font-semibold tabular-nums ${dirClasses(dirTone(baaSpread.change_1m)).text}`}>
              {formatNumber(baaSpread.value, locale)}<span className="text-sm font-medium text-muted-foreground">%</span>
            </span>
          ) : <span className="text-base text-muted-foreground">{t("kpi.notConfigured")}</span>}
        </KpiCard>
        <KpiCard
          label={t("kpi.hyOas")}
          footer={
            <span className="flex flex-wrap items-center gap-x-1">
              <span>{hyOas?.attribution ?? t("kpi.iceNote")}</span>
              <span role="img" title={t("kpi.hyCitation")} aria-label={t("kpi.fullCitation")}>
                <Info className="h-3 w-3" aria-hidden />
              </span>
              {hyOas?.observation_date ? <span>· {t("kpi.observed")} {hyOas.observation_date}</span> : null}
              <span>· {t("kpi.displayOnlyNote")}</span>
            </span>
          }
          data-testid="credit-hy-oas"
        >
          {hyOas?.value !== null && hyOas?.value !== undefined ? (
            <span className={`text-2xl font-semibold tabular-nums ${dirClasses(dirTone(hyOas.change_1m)).text}`}>
              {formatNumber(hyOas.value, locale)}<span className="text-sm font-medium text-muted-foreground">%</span>
            </span>
          ) : <span className="text-base text-muted-foreground">{t("kpi.notConfigured")}</span>}
        </KpiCard>
        <KpiCard
          label={t("kpi.igOas")}
          footer={
            <span className="flex flex-wrap items-center gap-x-1">
              <span>{igOas?.attribution ?? t("kpi.iceNote")}</span>
              <span role="img" title={t("kpi.igCitation")} aria-label={t("kpi.fullCitation")}>
                <Info className="h-3 w-3" aria-hidden />
              </span>
              {igOas?.observation_date ? <span>· {t("kpi.observed")} {igOas.observation_date}</span> : null}
            </span>
          }
          data-testid="credit-ig-oas"
        >
          {igOas?.value !== null && igOas?.value !== undefined ? (
            <span className={`text-2xl font-semibold tabular-nums ${dirClasses(dirTone(igOas.change_1m)).text}`}>
              {formatNumber(igOas.value, locale)}<span className="text-sm font-medium text-muted-foreground">%</span>
            </span>
          ) : <span className="text-base text-muted-foreground">{t("kpi.notConfigured")}</span>}
        </KpiCard>
      </section>

      {risk && dashboardQ.data ? (
        <section className="rounded-md border border-hairline p-3 text-xs text-muted-foreground" data-testid="overview-risk-evidence">
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            <span>{t("risk:evidence.title")}: <strong className="font-medium text-foreground">{risk.evidence_state === null ? t("risk:evidence.unknown") : t(`risk:${RISK_EVIDENCE_KEYS[risk.evidence_state]}`)}</strong></span>
            <span>{t("risk:evidence.coverage")}: <strong className="font-medium tabular-nums text-foreground">{risk.evidence_coverage === null ? t("common:data.na") : formatRatio(risk.evidence_coverage, locale)}</strong></span>
            <span>{t("risk:evidence.scoreRange")}: <strong className="font-medium tabular-nums text-foreground">{risk.score_lower_bound === null || risk.score_upper_bound === null ? t("common:data.na") : `${formatNumber(risk.score_lower_bound, locale)}–${formatNumber(risk.score_upper_bound, locale)}`}</strong></span>
          </div>
          <p className="mt-2 leading-relaxed">{t("risk:evidence.explanation")}</p>
        </section>
      ) : null}

      {/* Open chart region: risk trend (no card chrome) */}
      <section className="border-t border-hairline pt-4" data-testid="risk-trend-section">
        <div className="mb-2 flex items-baseline gap-2">
          <h2 className="text-sm font-medium text-foreground">{t("trend.title")}</h2>
        </div>
        {trendQ.data?.some((point) => point.evidence_state === undefined) ? <p className="mb-2 text-xs text-muted-foreground">{t("trend.legacyEvidenceUnknown")}</p> : null}
        {trendQ.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : trendQ.isError ? (
          <ErrorState onRetry={trendQ.refetch} />
        ) : trendQ.data && trendQ.data.some((point) => point.evidence_state !== "insufficient_evidence") ? (
          <RiskTrendChart points={trendQ.data.map((point) => ({ ...point, total_score: point.evidence_state === "insufficient_evidence" ? null : point.total_score }))} />
        ) : (
          <EmptyState title={t("trend.empty")} />
        )}
      </section>

      {/* Open chart region: cross-asset heatmap (no card chrome) */}
      <section className="border-t border-hairline pt-4" data-testid="cross-asset-section">
        <div className="mb-2 flex items-baseline gap-2">
          <h2 className="text-sm font-medium text-foreground">{t("heatmap.title")}</h2>
        </div>
        {dashboardQ.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : dashboardQ.isError ? (
          <ErrorState onRetry={dashboardQ.refetch} />
        ) : heatmapCells.length > 0 ? (
          <AssetHeatmap cells={heatmapCells} />
        ) : (
          <EmptyState title={t("heatmap.empty")} />
        )}
        <div className="mt-3">
          <CoinMetricsAttribution show={dashboard?.cross_asset.some((asset) => asset.category === "crypto" && asset.source === "coinmetrics") ?? false} />
        </div>
      </section>

      {/* Top drivers (hairline table) + upcoming catalysts */}
      <section className="grid grid-cols-1 gap-6 border-t border-hairline pt-4 lg:grid-cols-2" data-testid="drivers-catalysts-section">
        <div>
          <h2 className="mb-2 text-sm font-medium text-foreground">{t("risk:drivers.title")}</h2>
          {dashboardQ.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : dashboardQ.isError ? (
            <ErrorState onRetry={dashboardQ.refetch} />
          ) : topDrivers.length > 0 ? (
            <div data-testid="top-drivers">
              <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-hairline pb-1.5 text-xs text-muted-foreground">
                <span>{t("risk:drivers.title")}</span>
                <span className="text-right">{t("risk:drivers.contribution")}</span>
                <span className="w-16 text-right">{t("kpi.colDelta")}</span>
              </div>
              {topDrivers.map((driver) => {
                const contribTone = riskTrendTone(driver.contribution);
                return (
                  <div
                    key={`${driver.dimension_key}-${driver.indicator_key}`}
                    className="grid grid-cols-[1fr_auto_auto] items-center gap-3 border-b border-hairline/60 py-2 last:border-0"
                    data-testid="risk-driver"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">
                        {t(`risk:${RISK_INDICATOR_KEYS[driver.indicator_key] ?? "indicatorNames.unknown"}`)}
                        {driver.is_proxy ? <Badge variant="outline" className="ml-1.5 px-1 py-0 text-xs">{t("risk:indicator.proxy")}</Badge> : null}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {t(`risk:${RISK_DIMENSION_KEYS[driver.dimension_key as RiskDimensionKey]}`)}
                      </p>
                    </div>
                    <span className={`text-right text-xs font-semibold tabular-nums ${toneClasses(contribTone).text}`}>
                      {formatNumber(driver.contribution, locale)}
                    </span>
                    <span className="w-16 text-right text-xs tabular-nums text-muted-foreground">
                      {driver.change_1d === null || driver.change_1d === undefined
                        ? t("common:data.na")
                        : formatPctPoints(driver.change_1d, locale)}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState title={t("risk:drivers.none")} />
          )}
        </div>

        <div>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground">
            <CalendarClock className="h-4 w-4 text-muted-foreground" aria-hidden />
            {t("catalysts.title")}
          </h2>
          {dashboardQ.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : dashboardQ.isError ? (
            <ErrorState onRetry={dashboardQ.refetch} />
          ) : catalysts.length > 0 ? (
            <div className="flex flex-col gap-2" data-testid="catalysts">
              {catalysts.map((ev) => (
                <EventCard key={ev.id} event={ev} />
              ))}
            </div>
          ) : (
            <EmptyState title={t("catalysts.none")} />
          )}
        </div>
      </section>

      {/* Sectors (hairline rows) + important news */}
      <section className="grid grid-cols-1 gap-6 border-t border-hairline pt-4 lg:grid-cols-2" data-testid="sectors-news-section">
        <div>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground">
            <Layers className="h-4 w-4 text-muted-foreground" aria-hidden />
            {t("sectors.title")}
          </h2>
          {dashboardQ.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : dashboardQ.isError ? (
            <ErrorState onRetry={dashboardQ.refetch} />
          ) : sectorBaskets.length > 0 ? (
            <div data-testid="sector-performance">
              {sectorBaskets.map((s) => {
                const dTone = dirTone(s.change_1d);
                // #102 (C-1): labels come from the themes namespace, keyed by the canonical key.
                const label = t(`themes:${s.key}`, { defaultValue: t("common:empty.translationUnavailable") });
                return (
                  <div
                    key={s.key}
                    className="flex items-center justify-between border-b border-hairline/60 py-1.5 last:border-0"
                  >
                    <span className="text-xs text-foreground">{label}</span>
                    <span className={`text-xs font-semibold tabular-nums ${dirClasses(dTone).text}`}>
                      {s.change_1d === null ? t("common:data.na") : formatChange(s.change_1d, locale)}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState title={t("sectors.none")} />
          )}
        </div>

        <div>
          <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 className="flex items-center gap-2 text-sm font-medium text-foreground">
              <ArrowUpRight className="h-4 w-4 text-muted-foreground" aria-hidden />
              {t("news.title")}
            </h2>
            <span className="text-xs text-muted-foreground">{t("news.attribution")}</span>
          </div>
          {newsQ.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : newsQ.isError ? (
            <ErrorState onRetry={newsQ.refetch} />
          ) : topNews.length > 0 ? (
            <div className="flex flex-col gap-2" data-testid="important-news">
              {topNews.map((item) => (
                <NewsCard key={item.id} item={item} />
              ))}
            </div>
          ) : (
            <EmptyState title={t("news.none")} />
          )}
        </div>
      </section>

      {/* Mono status footer + compliance disclaimer */}
      {dashboardQ.data ? (
        <footer className="flex flex-col gap-1 border-t border-hairline pt-2">
          <div className="flex flex-wrap gap-4 font-mono text-xs text-muted-foreground">
            <span>
              {t("common:data.quality")}: <span className="tabular-nums">{formatRatio(dashboardQ.data.data_quality, locale)}</span>
            </span>
            <span className="ml-auto">{risk?.model_version}</span>
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">{t("common:footer.disclaimer")}</p>
        </footer>
      ) : null}
    </div>
  );
}
