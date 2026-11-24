import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useDataset } from "@/hooks/useDataset";
import type { MacroEnvelope } from "@/schemas";
import { MacroIndicatorCard } from "@/components/macro/MacroIndicatorCard";
import { RateExpectationCard } from "@/components/macro/RateExpectationCard";
import { MacroChart } from "@/charts/MacroChart";
import { MacroHistoryChart, type MacroBundle } from "@/charts/MacroHistoryChart";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusBadge } from "@/components/layout/StatusBadge";

// #96 (uses #84): seven groups incl. the new `volatility` (VIX is an implied-volatility
// index, not a rate) — mirrors risk_model.yaml's dimensions.
const SECTIONS = ["rates", "credit", "volatility", "inflation", "labor", "liquidity", "fx", "industry"] as const;
type MacroGroup = (typeof SECTIONS)[number];

/** history/macro/{group}.{slice}.json — sparse column-oriented per-series history (#84 §3). */
const MacroBundleSchema = z.record(z.string(), z.object({ d: z.array(z.string()), v: z.array(z.number()) }));

/**
 * MacroPage: macro page (rates / credit / volatility / inflation / labor / liquidity / fx
 * + FedWatch + cross-sectional chart + per-group 30d history chart).
 */
export default function MacroPage() {
  const { t, i18n } = useTranslation("macro");
  const macroQ = useDataset<MacroEnvelope>("macro");
  const [historyGroup, setHistoryGroup] = useState<MacroGroup>("fx");
  const [historySlice, setHistorySlice] = useState<"30d" | "90d">("90d");

  const chartItems = useMemo(
    () => macroQ.data?.payload.rates.concat(macroQ.data.payload.credit)
      .filter((ind) => ind.value !== null)
      .map((ind) => ({
        label: t(`indicatorNames.${ind.key}`, { defaultValue: t("indicatorNames.unknown") }),
        value: ind.value as number,
        unit: ind.unit,
      })) ?? [],
    [macroQ.data, t],
  );
  const historyQ = useDataset<MacroBundle>(
    "macro",
    { slice: `${historyGroup}.${historySlice}` },
    MacroBundleSchema,
  );
  const historyUnits = new Map((macroQ.data?.payload[historyGroup] ?? []).map((indicator) => [indicator.key.toUpperCase(), indicator.unit]));
  const historyBundle: MacroBundle = Object.fromEntries(
    Object.entries(historyQ.data ?? {}).map(([series, columns]) => [series, { ...columns, unit: historyUnits.get(series) ?? "level" }]),
  );
  const historyGroups = SECTIONS.filter((group) =>
    (macroQ.data?.payload[group] ?? []).some((indicator) => indicator.value !== null),
  );
  const isMonthlyGroup = (group: MacroGroup) => {
    const available = macroQ.data?.payload[group].filter((indicator) => indicator.value !== null) ?? [];
    return available.length > 0 && available.every((indicator) => indicator.observation_period !== null);
  };
  const monthlyHistory = isMonthlyGroup(historyGroup);
  const selectHistoryGroup = (group: MacroGroup) => {
    setHistoryGroup(group);
    setHistorySlice(isMonthlyGroup(group) ? "90d" : "30d");
  };

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-lg font-semibold text-foreground" data-testid="page-title">
          {t("title")}
        </h1>
        <p className="text-xs text-muted-foreground">{t("subtitle")}</p>
        {macroQ.data ? <span className="ml-auto"><StatusBadge dataset="macro" generatedAt={macroQ.data.generated_at} status={macroQ.data.freshness_status} fromCache={macroQ.data.provenance?.from_cache} withDescription /></span> : null}
      </header>

      {macroQ.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : macroQ.isError ? (
        <ErrorState onRetry={macroQ.refetch} />
      ) : macroQ.data ? (
        <>
          <section className="border-t border-hairline pt-4">
            <h2 className="mb-2 text-sm font-medium text-foreground">{t("chart.title")}</h2>
            <MacroChart
              items={chartItems}
            />
          </section>

          <div className="flex flex-col gap-4 border-t border-hairline pt-4">
            {SECTIONS.map((section) => {
              const indicators = macroQ.data?.payload[section] ?? [];
              if (indicators.length === 0) {
                return null;
              }
              const publicIndicators = indicators.filter((indicator) => indicator.availability_reason !== "public_source_unlicensed");
              const hasRestrictedRows = publicIndicators.length < indicators.length;
              const statusPath = i18n.language.startsWith("zh") ? "/zh/status" : "/en/status";
              return (
                <section key={section} data-testid={`section-${section}`}>
                  <h2 className="mb-2 text-sm font-medium text-foreground">{t(`section.${section}`)}</h2>
                  {publicIndicators.length > 0 ? <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {publicIndicators.map((ind) => (
                      <MacroIndicatorCard key={ind.key} indicator={ind} />
                    ))}
                  </div> : null}
                  {hasRestrictedRows ? <p className="text-xs text-muted-foreground" data-testid={`section-${section}-restricted`}>
                    {t("section.publicSourceRestricted")} <Link className="underline underline-offset-2 hover:text-foreground" to={statusPath}>{t("section.viewStatus")}</Link>
                  </p> : null}
                </section>
              );
            })}
          </div>

          <section className="border-t border-hairline pt-4" data-testid="section-history">
            <h2 className="mb-2 text-sm font-medium text-foreground">{t("history.title", { slice: historySlice })}</h2>
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              {historyGroups.map((group) => (
                <button
                  key={group}
                  type="button"
                  onClick={() => selectHistoryGroup(group)}
                  aria-pressed={historyGroup === group}
                  className={`rounded-full border px-2.5 py-0.5 text-xs min-h-[28px] transition-[color,background-color,border-color,opacity,transform] duration-motion-quick ease-motion-standard ${
                    historyGroup === group
                      ? "border-fresh-ok/40 bg-fresh-ok/10 text-fresh-ok"
                      : "border-hairline text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t(`section.${group}`)}
                </button>
              ))}
              <span className="ml-auto flex gap-1.5">
                {(monthlyHistory ? ["90d"] as const : ["30d", "90d"] as const).map((slice) => (
                  <button
                    key={slice}
                    type="button"
                    onClick={() => setHistorySlice(slice)}
                    aria-pressed={historySlice === slice}
                    className={`rounded-full border px-2.5 py-0.5 text-xs min-h-[28px] transition-[color,background-color,border-color,opacity,transform] duration-motion-quick ease-motion-standard ${
                      historySlice === slice
                        ? "border-fresh-ok/40 bg-fresh-ok/10 text-fresh-ok"
                        : "border-hairline text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {slice}
                  </button>
                ))}
              </span>
            </div>
            {historyQ.isLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : historyQ.isError ? (
              <ErrorState onRetry={historyQ.refetch} />
            ) : historyQ.data ? (
              <MacroHistoryChart bundle={historyBundle} />
            ) : (
              <EmptyState title={t("history.empty")} />
            )}
          </section>

          {macroQ.data.payload.fedwatch ? (
            <section className="border-t border-hairline pt-4" data-testid="section-fedwatch">
              <h2 className="mb-2 text-sm font-medium text-foreground">{t("fedwatch.title")}</h2>
              <RateExpectationCard fedwatch={macroQ.data.payload.fedwatch} />
            </section>
          ) : null}
        </>
      ) : (
        <EmptyState title={t("common:empty.title")} />
      )}
    </div>
  );
}
