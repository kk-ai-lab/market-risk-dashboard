import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useDataset } from "@/hooks/useDataset";
import { KEY_US_STOCKS } from "@/config/universe";
import type { EquitiesEnvelope, SectorsEnvelope } from "@/schemas";
import { EquityCard } from "@/components/equities/EquityCard";
import { displayMarketUnit } from "@/charts/MarketSnapshot";
import { MemorySectorTable } from "@/components/equities/MemorySectorTable";
import { AShareCard } from "@/components/equities/AShareCard";
import { AssetCard } from "@/components/cross-asset/AssetCard";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusBadge } from "@/components/layout/StatusBadge";
import { displayLocalizedValue } from "@/lib/displayLanguage";
import { formatNumber, formatPctPoints } from "@/lib/format";
import { FreshnessDocument } from "@/schemas";

/**
 * EquitiesPage: equities page (Equity Monitor).
 * Scope (G2): Cross-Asset cards + 4-5 key stocks (NVDA/AVGO/MU/AMD/TSLA) + A-share memory.
 */
export default function EquitiesPage() {
  const { t, i18n } = useTranslation("equities");
  const locale = i18n.language;
  const equitiesQ = useDataset<EquitiesEnvelope>("equities");
  const sectorsQ = useDataset<SectorsEnvelope>("sectors");
  const freshnessQ = useDataset<FreshnessDocument>("freshness", {}, FreshnessDocument);

  const usAssets =
    equitiesQ.data?.payload.assets.filter((a) => a.market === "US" && KEY_US_STOCKS.includes(a.symbol)) ?? [];
  const cnAssets = equitiesQ.data?.payload.assets.filter((a) => a.market === "CN") ?? [];
  const benchmarks = equitiesQ.data?.payload.benchmarks ?? [];
  const memory = sectorsQ.data?.payload.memory ?? null;

  // Cross-Asset cards: key stocks + crypto + sector proxies
  const crossAssets = equitiesQ.data?.payload.assets.filter((a) => KEY_US_STOCKS.includes(a.symbol)) ?? [];
  const hasPrices = crossAssets.length > 0 || usAssets.length > 0 || cnAssets.length > 0 || benchmarks.length > 0;
  const localeSegment = locale.startsWith("zh") ? "zh" : "en";

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-lg font-semibold text-foreground" data-testid="page-title">
          {t("title")}
        </h1>
        <p className="text-xs text-muted-foreground">{t("subtitle")}</p>
        {equitiesQ.data && hasPrices ? <span className="ml-auto"><StatusBadge dataset="equities" generatedAt={equitiesQ.data.generated_at} status={equitiesQ.data.freshness_status} fromCache={equitiesQ.data.provenance?.from_cache} withDescription /></span> : null}
      </header>

      {equitiesQ.isLoading || freshnessQ.isLoading ? <Skeleton className="h-32 w-full" /> : equitiesQ.isError ? <ErrorState onRetry={equitiesQ.refetch} /> : !hasPrices ? (
        <div>
          <EmptyState title={t("section.empty")} message={t(freshnessQ.data?.datasets.equities?.reason.code === "source_policy_excluded" ? "empty.publicPricesUnavailable" : "empty.pricesUnavailable")} className="min-h-28" data-testid="equities-public-source-empty" />
          <Link className="mt-2 inline-flex text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground" to={`/${localeSegment}/status`}>{t("empty.viewStatus")}</Link>
        </div>
      ) : (
      <>
      {benchmarks.length > 0 ? <section className="border-t border-hairline pt-4" data-testid="public-equity-indices">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-medium text-foreground">{t("section.indices")}</h2>
          <p className="text-xs text-muted-foreground">{t("section.indicesNote")}</p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {benchmarks.map((benchmark) => (
            <div key={benchmark.symbol} className="rounded-md border border-border/70 p-3">
              <div className="flex items-baseline justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">{displayLocalizedValue(benchmark.name, benchmark.name_zh, locale)}</p>
                  <p className="font-mono text-xs text-muted-foreground">{benchmark.symbol}</p>
                </div>
                <p className="whitespace-nowrap font-mono text-sm font-semibold tabular-nums text-foreground">{formatNumber(benchmark.value, locale)} <span className="text-xs font-normal text-muted-foreground">{displayMarketUnit(benchmark.unit, locale, t)}</span></p>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{benchmark.observation_period} · <a href={benchmark.source_url} target="_blank" rel="noreferrer" title={benchmark.attribution} className="underline underline-offset-2 hover:text-foreground">{t("section.oecdSource")}</a></p>
              {benchmark.change_1m !== null ? <p className="mt-1 text-xs tabular-nums text-muted-foreground">{t("section.monthChange")}: {formatPctPoints(benchmark.change_1m, locale)}</p> : null}
            </div>
          ))}
        </div>
      </section> : null}

      {crossAssets.length > 0 ? <section className="border-t border-hairline pt-4">
        <h2 className="mb-2 text-sm font-medium text-foreground">{t("section.crossAsset")}</h2>
        {crossAssets.length > 0 ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5" data-testid="cross-asset-cards">
              {crossAssets.map((a) => (
                <AssetCard
                  key={a.symbol}
                  symbol={a.symbol}
                  name={displayLocalizedValue(a.name, a.name_zh, locale)}
                  value={a.price}
                  change1d={a.change_1d}
                  sub={a.currency}
                />
              ))}
            </div>
          ) : null}
      </section> : null}

      {/* Key US equities */}
      {usAssets.length > 0 ? <section className="border-t border-hairline pt-4" data-testid="section-us">
        <h2 className="mb-2 text-sm font-medium text-foreground">{t("section.us")}</h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3" data-testid="us-equity-cards">
          {usAssets.map((a) => (
            <EquityCard key={a.symbol} asset={a} />
          ))}
        </div>
      </section> : null}

      </>
      )}

      {memory || cnAssets.length > 0 ? <section className="border-t border-hairline pt-4" data-testid="section-memory">
        <MemorySectorTable assets={cnAssets} memory={memory} />
      </section> : sectorsQ.isLoading ? <Skeleton className="h-32 w-full" /> : sectorsQ.isError ? <ErrorState onRetry={sectorsQ.refetch} /> : null}

      {/* A-share cards (mobile; the long table is above in the desktop view, this adds card views) */}
      {cnAssets.length > 0 ? (
        <section className="border-t border-hairline pt-4" data-testid="section-ashare">
          <h2 className="mb-2 text-sm font-medium text-foreground">{t("section.aShare")}</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {cnAssets.map((a) => (
              <AShareCard key={a.symbol} asset={a} />
            ))}
          </div>
        </section>
      ) : null}

      {benchmarks.length > 0 && usAssets.length === 0 && cnAssets.length === 0 ? <p className="border-t border-hairline pt-4 text-xs text-muted-foreground" data-testid="individual-equities-unavailable">
        {t("empty.individualQuotesUnavailable")} <Link className="underline underline-offset-2 hover:text-foreground" to={`/${localeSegment}/status`}>{t("empty.viewStatus")}</Link>
      </p> : null}

    </div>
  );
}
