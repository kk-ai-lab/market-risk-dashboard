import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type * as echarts from "echarts/core";
import { chartTheme } from "./theme";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatDate, formatNumber, formatUnitSuffix } from "@/lib/format";
import { useReducedMotion } from "@/hooks/useReducedMotion";
import { useDocumentTheme } from "@/hooks/useDocumentTheme";

/** jsdom / no-canvas environment → fall back to HTML (avoid zrender animation loop crashes). */
function canvasSupported(): boolean {
  if (typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent)) return false;
  try {
    const canvas = document.createElement("canvas");
    return !!(canvas.getContext && canvas.getContext("2d"));
  } catch {
    return false;
  }
}

/** One series' sparse column-oriented history (history/macro/{group}.{30d,90d}.json, #96). */
export interface MacroSeriesColumns {
  d: string[];
  v: number[];
  unit?: string;
}

/** Bundle shape: series id → {d: dates, v: values} (each series carries its own date axis —
 * mixed-frequency groups need no null padding, #84 §3). */
export type MacroBundle = Record<string, MacroSeriesColumns>;

function MacroHistoryCompact({ bundle, locale }: { bundle: MacroBundle; locale: string }) {
  const { t } = useTranslation("macro");

  return (
    <div className="flex flex-col gap-2" data-testid="macro-history-compact">
      <p className="text-xs text-muted-foreground" data-testid="macro-history-scale-note">{t("history.independentScale")}</p>
      {Object.entries(bundle).map(([name, columns]) => {
        const values = columns.v;
        const dates = columns.d;
        if (values.length === 0 || dates.length === 0) return null;
        const min = Math.min(...values);
        const max = Math.max(...values);
        const range = max - min || 1;
        const firstTime = Date.parse(`${dates[0]}T00:00:00Z`);
        const lastTime = Date.parse(`${dates.at(-1)}T00:00:00Z`);
        const span = lastTime - firstTime || 1;
        const coordinates = values.map((value, index) => {
          const x = ((Date.parse(`${dates[index]}T00:00:00Z`) - firstTime) / span) * 100;
          const y = 24 - ((value - min) / range) * 18;
          return { x, y };
        });
        const points = coordinates.map(({ x, y }) => `${x},${y}`).join(" ");
        const lastIndex = values.length - 1;
        const lastPoint = coordinates.at(-1) ?? { x: 0, y: 14 };
        const unit = columns.unit ? formatUnitSuffix(columns.unit, locale) || columns.unit : "";
        const precision = columns.unit === "usd_billions" ? 6 : 2;

        return (
          <div key={name} className="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-3 rounded-md border border-hairline px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-xs font-medium text-foreground">{name}</p>
              <p className="text-xs tabular-nums text-muted-foreground">
                {formatNumber(values[lastIndex], locale, precision)}{unit ? ` ${unit}` : ""} · {formatDate(dates[lastIndex], locale)}
              </p>
              <span className="sr-only">
                {t("history.trendRange", {
                  startDate: formatDate(dates[0], locale),
                  startValue: `${formatNumber(values[0], locale, precision)}${unit ? ` ${unit}` : ""}`,
                  endDate: formatDate(dates[lastIndex], locale),
                  endValue: `${formatNumber(values[lastIndex], locale, precision)}${unit ? ` ${unit}` : ""}`,
                })}
              </span>
            </div>
            <svg viewBox="0 0 100 28" className="h-7 w-20 text-primary" preserveAspectRatio="none" aria-hidden="true">
              {values.length > 1 ? <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" /> : null}
              <circle cx={lastPoint.x} cy={lastPoint.y} r="2.5" fill="currentColor" />
            </svg>
          </div>
        );
      })}
    </div>
  );
}

/**
 * MacroHistoryChart: one group's 30d/90d history as a multi-series line chart
 * (ECharts LineChart, imported on demand). Sparse per-series date axes are joined onto a
 * union date axis (missing values render as gaps). No canvas → HTML fallback table.
 */
export interface MacroHistoryChartProps {
  bundle: MacroBundle;
  height?: number;
}

export function MacroHistoryChart({ bundle, height = 260 }: MacroHistoryChartProps) {
  const { t, i18n } = useTranslation("macro");
  const locale = i18n.language;
  const reducedMotion = useReducedMotion();
  const theme = useDocumentTheme();
  const ref = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"echarts" | "fallback">("echarts");
  const [isNarrow, setIsNarrow] = useState(() => typeof window !== "undefined" && window.innerWidth < 640);
  const units = useMemo(() => Array.from(new Set(Object.values(bundle).map((series) => series.unit ?? "level"))), [bundle]);
  const compactMultiUnit = isNarrow && units.length > 2;

  useEffect(() => {
    const onResize = () => setIsNarrow(window.innerWidth < 640);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!ref.current || Object.keys(bundle).length === 0) return;
    if (compactMultiUnit) return;
    setMode("echarts");
    if (!canvasSupported()) {
      setMode("fallback");
      return;
    }
    let active = true;
    let chart: echarts.ECharts | undefined;
    const allDates = Array.from(new Set(Object.values(bundle).flatMap((s) => s.d))).sort();
    const unitIndex = new Map(units.map((unit, index) => [unit, index]));
    const leftAxisCount = Math.ceil(units.length / 2);
    const rightAxisCount = Math.floor(units.length / 2);
    const unitLabel = (unit: string) => formatUnitSuffix(unit, locale) || unit;
    const series = Object.entries(bundle).map(([name, cols]) => {
      const hasLongGaps = cols.d.some((day, index) => index > 0 &&
        Date.parse(`${day}T00:00:00Z`) - Date.parse(`${cols.d[index - 1]}T00:00:00Z`) >= 5 * 24 * 60 * 60 * 1000);
      const showSymbol = cols.d.length <= 3 || hasLongGaps;
      return {
        name,
        type: "line" as const,
        showSymbol,
        symbolSize: showSymbol ? 5 : 0,
        yAxisIndex: unitIndex.get(cols.unit ?? "level") ?? 0,
        connectNulls: false,
        data: allDates.map((d) => {
          const idx = cols.d.indexOf(d);
          return idx >= 0 ? cols.v[idx] : null;
        }),
      };
    });
    const onResize = () => chart?.resize();
    window.addEventListener("resize", onResize);
    void import("./echarts")
      .then(({ default: charting }) => {
        if (!active || !ref.current) return;
        chart = charting.init(ref.current);
        const th = chartTheme();
        chart.setOption({
          animation: !reducedMotion,
          animationDuration: reducedMotion ? 0 : 180,
          animationDurationUpdate: reducedMotion ? 0 : 120,
          grid: {
            left: 8 + Math.max(0, leftAxisCount - 1) * 56,
            right: 16 + Math.max(0, rightAxisCount - 1) * 56,
            top: 28,
            bottom: 8,
            containLabel: true,
          },
          legend: { textStyle: { color: th.axis, fontSize: 10 }, top: 2 },
          tooltip: {
            trigger: "axis",
            formatter: (params: unknown) => {
              const list = params as Array<{
                axisValue: string;
                seriesName: string;
                data: number | null;
              }>;
              if (!Array.isArray(list) || list.length === 0) return "";
              const rows = list
                .filter((p) => p.data !== null)
                .map((p) => {
                  const unit = bundle[p.seriesName]?.unit ?? "level";
                  const suffix = unitLabel(unit);
                  return `${p.seriesName}: <b>${formatNumber(p.data as number, locale)}${suffix ? ` ${suffix}` : ""}</b>`;
                })
                .join("<br/>");
              return `${formatDate(list[0].axisValue, locale)}<br/>${rows}`;
            },
          },
          xAxis: {
            type: "category",
            data: allDates,
            axisLine: { lineStyle: { color: th.grid } },
            axisLabel: { color: th.axis, fontSize: 10 },
          },
          yAxis: units.map((unit, index) => ({
            type: "value",
            scale: true,
            name: unitLabel(unit),
            nameLocation: "middle",
            nameRotate: 90,
            nameGap: 40,
            position: index % 2 === 0 ? "left" : "right",
            offset: Math.floor(index / 2) * 56,
            axisLabel: { color: th.axis, fontSize: 10 },
            nameTextStyle: { color: th.axis, fontSize: 10 },
            splitLine: index === 0 ? { lineStyle: { color: th.grid } } : { show: false },
          })),
          series,
        });
      })
      .catch(() => {
        if (active) setMode("fallback");
      });
    return () => {
      active = false;
      window.removeEventListener("resize", onResize);
      chart?.dispose();
    };
  }, [bundle, compactMultiUnit, locale, reducedMotion, theme, units]);

  if (Object.keys(bundle).length === 0) {
    return <EmptyState title={t("history.empty")} />;
  }

  if (compactMultiUnit) {
    return <MacroHistoryCompact bundle={bundle} locale={locale} />;
  }

  if (mode === "fallback") {
    return (
      <div className="overflow-x-auto" data-testid="macro-history-fallback">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
              <th className="py-1.5 pr-2 font-medium">{t("history.series")}</th>
              <th className="py-1.5 pr-2 font-medium">{t("history.latest")}</th>
              <th className="py-1.5 font-medium">{t("history.date")}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(bundle).map(([name, cols]) => {
              const lastIdx = cols.v.length - 1;
              return (
                <tr key={name} className="border-b border-border/50 last:border-0">
                  <td className="py-1.5 pr-2 font-mono">{name}</td>
                  <td className="py-1.5 pr-2 tabular-nums">
                    {lastIdx >= 0 ? `${formatNumber(cols.v[lastIdx], locale)}${cols.unit ? ` ${formatUnitSuffix(cols.unit, locale) || cols.unit}` : ""}` : "—"}
                  </td>
                  <td className="py-1.5 tabular-nums text-muted-foreground">
                    {lastIdx >= 0 ? formatDate(cols.d[lastIdx], locale) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return <div ref={ref} style={{ height }} data-testid="macro-history-chart" />;
}
