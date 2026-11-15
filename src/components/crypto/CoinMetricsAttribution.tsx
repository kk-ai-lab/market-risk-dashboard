import { useTranslation } from "react-i18next";

export function CoinMetricsAttribution({ show }: { show: boolean }) {
  const { t } = useTranslation("common");

  if (!show) return null;

  return (
    <p className="text-xs text-muted-foreground" data-testid="coinmetrics-attribution">
      {t("footer.coinMetricsAttribution")} <a className="underline" href="https://creativecommons.org/licenses/by-nc/4.0/" target="_blank" rel="noopener noreferrer">CC BY-NC 4.0</a> · <a className="underline" href="https://docs.coinmetrics.io/packages/coin-metrics-community-data" target="_blank" rel="noopener noreferrer">Coin Metrics</a>
    </p>
  );
}
