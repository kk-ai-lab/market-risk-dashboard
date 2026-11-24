import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type * as echarts from "echarts/core";
import { chartTheme } from "./theme";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatDate, formatNumber } from "@/lib/format";
import { useReducedMotion } from "@/hooks/useReducedMotion";
import { useDocumentTheme } from "@/hooks/useDocumentTheme";
import { CONTRACT_CONSTANTS } from "@/schemas/generated";

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

/**
 * RiskTrendChart: risk trend (history/risk/{slice}.json, ECharts line + area).
 * - Initial view only loads the 30d slice (architecture §1.7).
 * - Automatically falls back to an HTML table in jsdom / no-canvas environments (testable, accessible).
 */
export interface RiskTrendPointInput {
  date: string;
  total_score: number | null;
  model_version?: string;
  risk_level?: string;
  confidence?: number;
}

export interface RiskTrendChartProps {
  points: RiskTrendPointInput[];
  height?: number;
}

interface RiskTrendDisplayPoint extends RiskTrendPointInput {
  isGap?: true;
}

const MODEL_SERIES_STYLES = (["circle", "rect", "triangle", "diamond", "roundRect", "pin"] as const)
  .flatMap((symbol) => ["solid", "dashed", "dotted"].map((lineType) => ({ symbol, lineType })));

function recordedModelVersion(point: RiskTrendPointInput): string | undefined {
  return point.model_version?.trim() ? point.model_version : undefined;
}

function insertDateGaps(points: RiskTrendPointInput[]): RiskTrendDisplayPoint[] {
  const chronological = [...points].sort((left, right) => left.date.localeCompare(right.date));
  return chronological.flatMap((point, index) => {
    if (index === 0) return [point];
    const previousPoint = chronological[index - 1];
    const previous = Date.parse(`${previousPoint.date}T00:00:00Z`);
    const current = Date.parse(`${point.date}T00:00:00Z`);
    if (!Number.isFinite(previous) || !Number.isFinite(current) || current - previous <= 86_400_000) return [point];
    return [{
      date: new Date(previous + 86_400_000).toISOString().slice(0, 10),
      total_score: null,
      model_version: previousPoint.model_version === point.model_version ? point.model_version : undefined,
      isGap: true,
    }, point];
  });
}

export function RiskTrendChart({ points, height = 260 }: RiskTrendChartProps) {
  const { t, i18n } = useTranslation("dashboard");
  const locale = i18n.language;
  const reducedMotion = useReducedMotion();
  const theme = useDocumentTheme();
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [mode, setMode] = useState<"echarts" | "fallback">("echarts");
  const [chartReady, setChartReady] = useState(0);
  const displayPoints = useMemo(() => insertDateGaps(points), [points]);
  const chartData = useMemo(() => {
    const groups = new Map<string, RiskTrendPointInput[]>();
    for (const point of points) {
      const version = recordedModelVersion(point);
      if (!version) continue;
      const group = groups.get(version);
      if (group) group.push(point);
      else groups.set(version, [point]);
    }
    const versioned = [...groups].map(([version, rows]) => ({ version, points: insertDateGaps(rows) }));
    const dates = [...new Set(versioned.flatMap((group) => group.points.map((point) => point.date)))].sort();
    return {
      dates,
      series: versioned.map((group) => {
        const scores = new Map(group.points.map((point) => [point.date, point.total_score]));
        return {
          version: group.version,
          scores: dates.map((day) => {
            const value = scores.get(day);
            return value === null || value === undefined ? null : Number(value.toFixed(2));
          }),
        };
      }),
    };
  }, [points]);
  const historyTable = (
    <table className="w-full min-w-[420px] text-left text-xs">
      <thead>
        <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
          <th className="px-2 py-1.5 font-medium">{t("trend.date")}</th>
          <th className="px-2 py-1.5 font-medium">{t("trend.modelVersion")}</th>
          <th className="px-2 py-1.5 text-right font-medium">{t("trend.score")}</th>
        </tr>
      </thead>
      <tbody>
        {displayPoints.map((point) => (
          <tr key={point.date} className="border-b border-border/50 last:border-0">
            <td className="px-2 py-1.5">{formatDate(point.date, locale)}</td>
            <td className="px-2 py-1.5 tabular-nums">
              {point.isGap ? t("trend.dataGap") : recordedModelVersion(point) ?? t("trend.modelVersionUnknown")}
            </td>
            <td className="px-2 py-1.5 text-right tabular-nums">
              {point.total_score === null ? t("common:data.na") : formatNumber(point.total_score, locale)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  useEffect(() => {
    if (!ref.current || chartData.series.length === 0) return;
    if (!canvasSupported()) {
      setMode("fallback");
      return;
    }
    let active = true;
    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);
    void import("./echarts")
      .then(({ default: charting }) => {
        if (!active || !ref.current) return;
        try {
          chartRef.current = charting.init(ref.current);
          setChartReady((ready) => ready + 1);
        } catch {
          setMode("fallback");
        }
      })
      .catch(() => {
        if (active) setMode("fallback");
      });
    return () => {
      active = false;
      window.removeEventListener("resize", onResize);
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [chartData.series.length, reducedMotion]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const th = chartTheme();
    chart.setOption(
      {
        animation: !reducedMotion,
        animationDuration: reducedMotion ? 0 : 180,
        animationDurationUpdate: reducedMotion ? 0 : 120,
        grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
        legend: {
          top: 0,
          type: "scroll",
          textStyle: { color: th.axis, fontSize: 10 },
        },
        tooltip: {
          trigger: "axis",
          renderMode: "richText",
          formatter: (params: unknown) => {
            const list = params as Array<{ axisValue: string; data: number | null; seriesName: string }>;
            if (!Array.isArray(list) || list.length === 0) return "";
            const scores = list.filter((point) => typeof point.data === "number" && Number.isFinite(point.data))
              .map((point) => `${point.seriesName}: ${formatNumber(point.data, locale)}`);
            return [formatDate(list[0].axisValue, locale), ...(scores.length ? scores : [t("common:data.na")])].join("\n");
          },
        },
        xAxis: {
          type: "category",
          data: chartData.dates,
          axisLine: { lineStyle: { color: th.grid } },
          axisLabel: { color: th.axis, fontSize: 10 },
        },
        yAxis: {
          type: "value",
          min: 0,
          max: 100,
          axisLabel: { color: th.axis, fontSize: 10 },
          splitLine: { lineStyle: { color: th.grid } },
        },
        series: chartData.series.map((series, index) => {
          const style = MODEL_SERIES_STYLES[index % MODEL_SERIES_STYLES.length];
          return ({
            id: series.version,
            name: t("trend.modelSeries", { version: series.version }),
            type: "line",
            smooth: true,
            showSymbol: true,
            symbol: style.symbol,
            symbolSize: 4,
            connectNulls: false,
            data: series.scores,
            lineStyle: { color: th.accent, width: 1.5, type: style.lineType },
            areaStyle: { color: th.accentSoft },
            // Risk thresholds in risk tones (the only saturated marks on the chart)
            markLine: index === 0 ? {
              silent: true,
              symbol: "none",
              label: { color: th.axis, fontSize: 10 },
              data: [
                { yAxis: CONTRACT_CONSTANTS.risk_level_thresholds.high_risk.gte, lineStyle: { color: th.riskSevere, type: "dashed", width: 1 } },
                { yAxis: CONTRACT_CONSTANTS.risk_level_thresholds.caution.gte, lineStyle: { color: th.riskCaution, type: "dashed", width: 1 } },
              ],
            } : undefined,
          });
        }),
      },
      { notMerge: true },
    );
  }, [chartData, locale, reducedMotion, t, chartReady, theme]);

  if (points.length === 0) {
    return (
      <EmptyState
        title={t("trend.empty")}
        message={t("trend.emptyHint")}
        data-testid="chart-empty"
      />
    );
  }

  if (mode === "fallback" || chartData.series.length === 0) {
    return (
      <>
        <p className="mb-2 text-xs text-muted-foreground">{t("trend.versionNote")}</p>
        <div className="overflow-x-auto rounded-md border border-border" data-testid="trend-fallback">
          {historyTable}
        </div>
      </>
    );
  }

  return (
    <>
      <p className="mb-2 text-xs text-muted-foreground">{t("trend.versionNote")}</p>
      <div ref={ref} style={{ height }} data-testid="risk-trend-chart" className="w-full" />
      <details className="mt-2 rounded-md border border-border px-3 py-2 text-xs">
        <summary className="cursor-pointer font-medium text-muted-foreground">
          {t("trend.details")}
        </summary>
        <div className="mt-2 overflow-x-auto">
          {historyTable}
        </div>
      </details>
    </>
  );
}
