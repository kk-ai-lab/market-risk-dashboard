import { useTranslation } from "react-i18next";
import { displayCryptoName, displayLocalizedValue } from "@/lib/displayLanguage";
import { formatChange, formatDate, formatNumber } from "@/lib/format";
import type { MarketSnapshotItem } from "@/schemas";

const CATEGORIES = ["equity_index", "crypto", "commodity", "rate", "fx"] as const;

function observationLabel(item: MarketSnapshotItem, locale: string, t: (key: string) => string) {
  if (item.observation_period) return `${t("marketSnapshot.monthly")} · ${item.observation_period}`;
  if (item.observation_date) return `${t(item.frequency === "monthly" ? "marketSnapshot.monthly" : "marketSnapshot.daily")} · ${formatDate(item.observation_date, locale)}`;
  return t(item.frequency === "monthly" ? "marketSnapshot.monthly" : "marketSnapshot.daily");
}

export function displayMarketUnit(
  unit: string,
  locale: string,
  t?: (key: string, options?: { defaultValue?: string }) => string,
) {
  if (!locale.startsWith("zh") || !t) return unit;
  return t(`marketSnapshot.units.${unit}`, { defaultValue: unit });
}

function formattedMarketValue(item: MarketSnapshotItem, locale: string, t: (key: string, options?: { defaultValue?: string }) => string) {
  const decimals = Math.abs(item.value) < 1 ? 6 : 2;
  const value = formatNumber(item.value, locale, decimals);
  if (item.unit === "USD") return `$${value}`;
  return `${value} ${displayMarketUnit(item.unit, locale, t)}`;
}

function marketLabel(item: MarketSnapshotItem, locale: string, t: (key: string, options?: { defaultValue?: string }) => string) {
  if (item.category === "crypto") return displayCryptoName(item.symbol, item.label, locale);
  if (item.category === "rate" || item.category === "fx") {
    return t(`macro:indicatorNames.${item.symbol.toLowerCase()}`, { defaultValue: t("marketSnapshot.unknown") });
  }
  return displayLocalizedValue(item.label, item.label_zh, locale);
}

function sourceLabel(source: string, t: (key: string, options?: { defaultValue?: string }) => string) {
  const sourceKeys: Record<string, string> = {
    OECD: "oecd",
    "OECD Main Economic Indicators": "oecd",
    "World Bank Pink Sheet": "worldBankPinkSheet",
    "Coin Metrics": "coinMetrics",
    coinmetrics: "coinMetrics",
  };
  const key = sourceKeys[source];
  if (key) return t(`marketSnapshot.sources.${key}`);
  return t(`macro:sources.${source}`, { defaultValue: t("marketSnapshot.sources.publicSource") });
}

export function MarketSnapshot({ items }: { items: MarketSnapshotItem[] }) {
  const { t, i18n } = useTranslation("dashboard");
  const locale = i18n.language;
  const groups = CATEGORIES.map((category) => [
    category,
    items.filter((item) => item.category === category),
  ] as const).filter(([, categoryItems]) => categoryItems.length > 0);

  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground" data-testid="market-snapshot-empty">{t("marketSnapshot.empty")}</p>;
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="market-snapshot">
      {groups.map(([category, categoryItems]) => (
        <section key={category} className="min-w-0 rounded-md border border-border/70 bg-surface-2/20 p-3" data-testid="market-snapshot-category">
          <header className="mb-1.5 flex items-baseline justify-between gap-2 border-b border-border/60 pb-1.5">
            <h3 className="text-xs font-semibold text-foreground">{t(`marketSnapshot.categories.${category}`)}</h3>
            <span className="text-xs tabular-nums text-muted-foreground">{categoryItems.length}</span>
          </header>
          <div>
            {categoryItems.map((item) => {
              const label = marketLabel(item, locale, t);
              const source = sourceLabel(item.source, t);
              return (
                <div key={`${item.category}-${item.symbol}`} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 border-b border-border/40 py-2 last:border-0" data-testid="market-snapshot-item">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-foreground" title={label}>{label}</p>
                    <p className="truncate font-mono text-xs text-muted-foreground">{item.symbol}</p>
                  </div>
                  <div className="text-right">
                    <p className="whitespace-nowrap font-mono text-xs font-semibold tabular-nums text-foreground">{formattedMarketValue(item, locale, t)}</p>
                    {item.change_percent !== null && item.change_percent !== undefined && item.change_period ? (
                      <p className="text-xs tabular-nums text-muted-foreground">{formatChange(item.change_percent, locale)} · {t(`marketSnapshot.periods.${item.change_period}`)}</p>
                    ) : null}
                  </div>
                  <p className="col-span-2 mt-0.5 truncate text-xs text-muted-foreground" title={item.attribution ?? `${source} · ${observationLabel(item, locale, t)}`}>
                    {observationLabel(item, locale, t)} · {item.source_url ? (
                      <a href={item.source_url} target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-foreground">{source}</a>
                    ) : source}
                  </p>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
