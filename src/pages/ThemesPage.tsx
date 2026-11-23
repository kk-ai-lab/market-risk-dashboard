import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { useDataset } from "@/hooks/useDataset";
import { CONTRACT_CONSTANTS, type CommoditiesEnvelope, type CryptoEnvelope, type EnergyEnvelope, type EquitiesEnvelope, type SectorsEnvelope } from "@/schemas";
import { MemorySectorTable } from "@/components/equities/MemorySectorTable";
import { AssetCard } from "@/components/cross-asset/AssetCard";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusBadge } from "@/components/layout/StatusBadge";
import { CoinMetricsAttribution } from "@/components/crypto/CoinMetricsAttribution";
import { formatChange, formatCompactNumber, formatDateTime, formatRatio } from "@/lib/format";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent } from "@/components/ui/Card";
import { dirTone, dirClasses } from "@/lib/riskColors";
import { displayCryptoName, displayLocalizedValue } from "@/lib/displayLanguage";
import { EnergyContextCard } from "@/components/energy/EnergyContextCard";

/**
 * ThemesPage: themes page (semis / memory (incl. 11 A-shares) / commodities / crypto).
 * Shows degraded when T03 is degraded (A-share collection failure → notice + empty state).
 *
 * #93: the 20 themes render from sectors.json themes — labels via t(themes.<key>),
 * percentile_1y as a quintile band (or "warming up N/100" below min_obs), constituents as
 * symbol chips.
 *
 * #118: commodities (gold/silver/copper/oil) render from commodities.json — replacing the
 * old "Metals data not yet connected" placeholder.
 */

/** One theme card: label, 1d/1M changes, percentile band, constituent chips (#93). */
function ThemeCard({ theme }: { theme: { key: string; change_1d: number | null; change_1m: number | null; percentile_1y: number | null; percentile_1y_obs: number; constituents: string[] } }) {
  const { t } = useTranslation("themes");
  const [constituentsOpen, setConstituentsOpen] = useState(false);
  const dTone = dirTone(theme.change_1d);
  const pct = theme.percentile_1y;
  const band =
    pct === null
      ? null
      : pct < 20
        ? { label: "percentile.veryLow", cls: "text-muted-foreground" }
        : pct < 40
          ? { label: "percentile.low", cls: "text-muted-foreground" }
          : pct < 60
            ? { label: "percentile.normal", cls: "text-foreground" }
            : pct < 80
              ? { label: "percentile.high", cls: "text-foreground" }
              : { label: "percentile.veryHigh", cls: "text-foreground" };
  return (
    <Card data-testid={`theme-card-${theme.key}`}>
      <CardContent className="flex flex-col gap-2 p-3">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-xs font-medium text-foreground">
            {t(`${theme.key}`, { defaultValue: t("common:empty.translationUnavailable") })}
          </span>
          <span className={`shrink-0 text-xs font-semibold tabular-nums ${dirClasses(dTone).text}`}>
            {theme.change_1d === null ? t("common:data.na") : `${theme.change_1d > 0 ? "+" : ""}${theme.change_1d}%`}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
          <span>{t("period.oneMonth")}: {theme.change_1m === null ? t("common:data.na") : `${theme.change_1m > 0 ? "+" : ""}${theme.change_1m}%`}</span>
          {band ? (
            <span className={band.cls}>{t(band.label)}</span>
          ) : theme.percentile_1y_obs > 0 ? (
            <span>{t("percentile.warming", { obs: theme.percentile_1y_obs })}</span>
          ) : null}
        </div>
        {theme.constituents.length > 0 ? (
          <div className="flex flex-col gap-1">
            <div className="flex flex-wrap gap-1">
              {(constituentsOpen ? theme.constituents : theme.constituents.slice(0, 8)).map((sym) => (
                <span key={sym} className="rounded border border-hairline px-1 py-0.5 font-mono text-xs text-muted-foreground">
                  {sym}
                </span>
              ))}
            </div>
            {theme.constituents.length > 8 ? (
              <button
                type="button"
                onClick={() => setConstituentsOpen((v) => !v)}
                aria-expanded={constituentsOpen}
                className="inline-flex min-h-[28px] items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <span>{constituentsOpen ? t("constituents.showFewer") : t("constituents.showAll", { count: theme.constituents.length })}</span>
                <ChevronRight className={`h-3.5 w-3.5 transition-transform ${constituentsOpen ? "rotate-90" : ""}`} aria-hidden />
              </button>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function ThemesPage() {
  const { t, i18n } = useTranslation("themes");
  const locale = i18n.language;
  const sectorsQ = useDataset<SectorsEnvelope>("sectors");
  const equitiesQ = useDataset<EquitiesEnvelope>("equities");
  const cryptoQ = useDataset<CryptoEnvelope>("crypto");
  const commoditiesQ = useDataset<CommoditiesEnvelope>("commodities");
  const energyQ = useDataset<EnergyEnvelope>("energy");

  const themes = sectorsQ.data?.payload.themes ?? [];
  const sectors = sectorsQ.data?.payload.sectors ?? [];
  const cnAssets = equitiesQ.data?.payload.assets.filter((a) => a.market === "CN") ?? [];
  const memory = sectorsQ.data?.payload.memory ?? null;
  const cryptoAssets = cryptoQ.data?.payload.assets ?? [];
  const commodityAssets = commoditiesQ.data?.payload.assets ?? [];

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-lg font-semibold text-foreground" data-testid="page-title">
          {t("title")}
        </h1>
        <p className="text-xs text-muted-foreground">{t("subtitle")}</p>
        {sectorsQ.data ? <span className="ml-auto"><StatusBadge dataset="sectors" generatedAt={sectorsQ.data.generated_at} status={sectorsQ.data.freshness_status} fromCache={sectorsQ.data.provenance?.from_cache} withDescription /></span> : null}
      </header>

      {/* Semiconductors */}
      <section className="border-t border-hairline pt-4" data-testid="section-semis">
        <h2 className="mb-2 text-sm font-medium text-foreground">{t("section.semis")}</h2>
        {sectorsQ.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : sectorsQ.isError ? (
          <ErrorState onRetry={sectorsQ.refetch} />
        ) : sectors.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {sectors.map((s) => (
              <AssetCard
                key={s.key}
                symbol={t(`${s.key}`, { defaultValue: t("common:empty.translationUnavailable") })}
                change1d={s.change_1d}
                sub={`${t("period.oneWeek")}: ${s.change_1w === null ? t("common:data.na") : `${s.change_1w > 0 ? "+" : ""}${s.change_1w}%`}`}
              />
            ))}
          </div>
        ) : (
          <EmptyState title={t("section.empty")} />
        )}
      </section>

      {/* Memory (incl. 11 A-shares) */}
      <section className="border-t border-hairline pt-4" data-testid="section-memory">
        <MemorySectorTable assets={cnAssets} memory={memory} />
      </section>

      {/* Commodities (gold/silver/copper/oil, #118) */}
      <section className="border-t border-hairline pt-4" data-testid="section-commodities">
        <h2 className="mb-2 text-sm font-medium text-foreground">{t("section.commodities")}</h2>
        {commoditiesQ.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : commoditiesQ.isError ? (
          <ErrorState onRetry={commoditiesQ.refetch} />
        ) : commodityAssets.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {commodityAssets.map((c) => (
              <AssetCard
                key={c.symbol}
                symbol={c.symbol}
                name={displayLocalizedValue(c.name, c.name_zh, locale)}
                value={c.price}
                change1d={c.frequency === "monthly" ? null : c.change_1d}
                showChange={c.frequency !== "monthly"}
                sub={c.frequency === "monthly"
                  ? `${t("commodities.monthlyAverage")} · ${c.observation_period ?? t("common:data.na")} · ${c.price_unit ?? "USD"}${c.change_1m === null ? "" : ` · ${t("commodities.monthChange")}: ${formatChange(c.change_1m, locale)}`}`
                  : `${t("period.oneWeek")}: ${c.change_1w === null ? t("common:data.na") : formatChange(c.change_1w, locale)} · ${t("period.oneMonth")}: ${c.change_1m === null ? t("common:data.na") : formatChange(c.change_1m, locale)}`}
              />
            ))}
          </div>
        ) : (
          <EmptyState title={t("commodities.na")} message={t("commodities.naHint")} />
        )}
        {commodityAssets.some((asset) => asset.frequency === "monthly") ? (
          <p className="mt-2 text-xs text-muted-foreground">
            {t("commodities.monthlyDisclosure")} <a className="underline underline-offset-2 hover:text-foreground" href="https://datacatalog.worldbank.org/search/dataset/0038238/commodity-prices-history-and-projections" target="_blank" rel="noreferrer">World Bank Pink Sheet · CC BY 4.0</a>.
          </p>
        ) : null}
      </section>

      <section className="border-t border-hairline pt-4" data-testid="section-energy">
        <EnergyContextCard
          dataset={energyQ.data}
          isLoading={energyQ.isLoading}
          isError={energyQ.isError}
          onRetry={energyQ.refetch}
        />
      </section>

      {/* Crypto */}
      <section className="border-t border-hairline pt-4" data-testid="section-crypto">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-medium text-foreground">{t("section.crypto")}</h2>
          {cryptoQ.data ? <StatusBadge dataset="crypto" generatedAt={cryptoQ.data.generated_at} status={cryptoQ.data.freshness_status} fromCache={cryptoQ.data.provenance?.from_cache} sourceUpdatedAt={cryptoQ.data.source_updated_at} sourceExpectedMinutes={cryptoQ.data.provenance?.provider === "coinmetrics" ? CONTRACT_CONSTANTS.coinmetrics_expected_interval_minutes : undefined} withDescription /> : null}
        </div>
        {cryptoQ.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : cryptoQ.isError ? (
          <ErrorState onRetry={cryptoQ.refetch} />
        ) : cryptoAssets.length > 0 ? (
          <>
            {cryptoAssets.some((asset) => asset.source === "coinmetrics") ? (
              <p className="mb-3 text-xs text-muted-foreground" data-testid="coinmetrics-disclosure">{t("crypto.referenceDisclosure")}</p>
            ) : null}
            <div className="mb-3">
              <CoinMetricsAttribution show={cryptoAssets.some((asset) => asset.source === "coinmetrics")} />
            </div>
            <div className="mb-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
              {cryptoQ.data?.payload.market_cap_total !== null &&
              cryptoQ.data?.payload.market_cap_total !== undefined ? (
                <span>
                  {t("crypto.marketCap")}:{" "}
                  <span className="font-semibold tabular-nums text-foreground">
                    {formatCompactNumber(cryptoQ.data.payload.market_cap_total, locale)}
                  </span>
                </span>
              ) : null}
              {cryptoQ.data?.payload.btc_dominance !== null && cryptoQ.data?.payload.btc_dominance !== undefined ? (
                <span>
                  {t("crypto.dominance")}:{" "}
                  <span className="font-semibold tabular-nums text-foreground">
                    {formatRatio(cryptoQ.data.payload.btc_dominance, locale)}
                  </span>
                </span>
              ) : null}
              {cryptoQ.data?.payload.sentiment ? (
                <Badge variant="outline" className="px-1.5 py-0 text-xs">
                  {t(`crypto.${cryptoQ.data.payload.sentiment}`)}
                </Badge>
              ) : null}
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {cryptoAssets.map((c) => (
                <div key={c.symbol}>
                <AssetCard
                  symbol={c.symbol}
                  name={displayCryptoName(c.symbol, c.name, locale)}
                  value={c.price}
                  change1d={c.change_1d}
                  sub={`${t("crypto.volume24h")}: ${c.volume_24h === null ? t("common:data.na") : formatCompactNumber(c.volume_24h, locale)}`}
                />
                {c.source === "coinmetrics" ? <p className="mt-1 text-xs tabular-nums text-muted-foreground">{t("crypto.observedAt")}: {c.updated_at ? formatDateTime(c.updated_at, locale) : t("common:data.na")}</p> : null}
                </div>
              ))}
            </div>
          </>
        ) : (
          <EmptyState title={t("section.empty")} />
        )}
      </section>

      {/* Theme list (20 themes, #93) */}
      <section className="border-t border-hairline pt-4" data-testid="section-themes">
        <h2 className="mb-2 text-sm font-medium text-foreground">{t("section.themes")}</h2>
        {themes.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {themes.map((th) => (
              <ThemeCard key={th.key} theme={th} />
            ))}
          </div>
        ) : (
          <EmptyState title={t("section.empty")} />
        )}
      </section>
    </div>
  );
}
