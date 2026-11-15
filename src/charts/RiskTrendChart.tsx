import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type * as echarts from "echarts/core";
import { chartTheme } from "./theme";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatDate, formatNumber } from "@/lib/format";
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
  risk_level?: string;
  confidence?: number;
}

export interface RiskTrendChartProps {
  points: RiskTrendPointInput[];
  height?: number;
}

export function RiskTrendChart({ points, height = 260 }: RiskTrendChartProps) {
  const { t, i18n } = useTranslation("dashboard");
  const locale = i18n.language;
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [mode, setMode] = useState<"echarts" | "fallback">("echarts");
  const [chartReady, setChartReady] = useState(0);
  const displayPoints = useMemo(() => points.flatMap((point, index) => {
    if (index === 0) return [point];
    const previous = Date.parse(`${points[index - 1].date}T00:00:00Z`);
    const current = Date.parse(`${point.date}T00:00:00Z`);
    if (!Number.isFinite(previous) || !Number.isFinite(current) || current - previous <= 86_400_000) return [point];
    return [{ date: new Date(previous + 86_400_000).toISOString().slice(0, 10), total_score: null }, point];
  }), [points]);
  const chartData = useMemo(
    () => ({
      dates: displayPoints.map((p) => p.date),
      scores: displayPoints.map((p) => p.total_score === null ? null : Number(p.total_score.toFixed(2))),
    }),
    [displayPoints],
  );

  useEffect(() => {
    if (!ref.current || points.length === 0) return;
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
  }, [points.length]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const th = chartTheme();
    chart.setOption(
      {
        grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
        tooltip: {
          trigger: "axis",
          formatter: (params: unknown) => {
            const list = params as Array<{ axisValue: string; data: number | null }>;
            if (!Array.isArray(list) || list.length === 0) return "";
            const p = list[0];
            return `${formatDate(p.axisValue, locale)}<br/>${t("trend.score")}: <b>${p.data === null ? t("common:data.na") : formatNumber(p.data, locale)}</b>`;
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
        series: [
          {
            name: t("trend.score"),
            type: "line",
            smooth: true,
            showSymbol: false,
            data: chartData.scores,
            lineStyle: { color: th.accent, width: 1.5 },
            areaStyle: { color: th.accentSoft },
            // Risk thresholds in risk tones (the only saturated marks on the chart)
            markLine: {
              silent: true,
              symbol: "none",
              label: { color: th.axis, fontSize: 10 },
              data: [
                { yAxis: CONTRACT_CONSTANTS.risk_level_thresholds.high_risk.gte, lineStyle: { color: th.riskSevere, type: "dashed", width: 1 } },
                { yAxis: CONTRACT_CONSTANTS.risk_level_thresholds.caution.gte, lineStyle: { color: th.riskCaution, type: "dashed", width: 1 } },
              ],
            },
          },
        ],
      },
      { notMerge: true },
    );
  }, [chartData, locale, t, chartReady]);

  if (points.length === 0) {
    return (
      <EmptyState
        title={t("trend.empty")}
        message={t("trend.emptyHint")}
        data-testid="chart-empty"
      />
    );
  }

  if (mode === "fallback") {
    return (
      <div className="overflow-x-auto rounded-md border border-border" data-testid="trend-fallback">
        <table className="w-full min-w-[420px] text-left text-xs">
          <thead>
            <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-2 py-1.5 font-medium">{t("trend.date")}</th>
              <th className="px-2 py-1.5 text-right font-medium">{t("trend.score")}</th>
            </tr>
          </thead>
          <tbody>
            {displayPoints.map((p) => (
              <tr key={p.date} className="border-b border-border/50 last:border-0">
                <td className="px-2 py-1.5">{formatDate(p.date, locale)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {p.total_score === null ? t("common:data.na") : formatNumber(p.total_score, locale)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <>
      <div ref={ref} style={{ height }} data-testid="risk-trend-chart" className="w-full" />
      <details className="mt-2 rounded-md border border-border px-3 py-2 text-xs">
        <summary className="cursor-pointer font-medium text-muted-foreground">
          {t("trend.details")}
        </summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                <th className="py-1.5 pr-2 font-medium">{t("trend.date")}</th>
                <th className="py-1.5 text-right font-medium">{t("trend.score")}</th>
              </tr>
            </thead>
            <tbody>
              {displayPoints.map((point) => (
                <tr key={point.date} className="border-b border-border/50 last:border-0">
                  <td className="py-1.5 pr-2">{formatDate(point.date, locale)}</td>
                  <td className="py-1.5 text-right tabular-nums">
                    {point.total_score === null ? t("common:data.na") : formatNumber(point.total_score, locale)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
