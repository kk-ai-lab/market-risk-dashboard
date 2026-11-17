import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { displayProvider } from "@/lib/displayLanguage";
import { formatDateTime } from "@/lib/format";
import type { CalendarSourceStatus } from "@/schemas";

export function CalendarSourceStatusList({ sources }: { sources: CalendarSourceStatus[] }) {
  const { t, i18n } = useTranslation("calendar");

  return (
    <section className="border-y border-hairline py-3" aria-label={t("coverage.title")} data-testid="calendar-source-status">
      <h2 className="text-sm font-medium">{t("coverage.title")}</h2>
      <p className="mt-1 text-xs text-muted-foreground">{t("coverage.description")}</p>
      {sources.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">{t("coverage.unavailable")}</p>
      ) : (
        <ul className="mt-2 grid gap-2 md:grid-cols-3">
          {sources.map((source) => (
            <li key={source.source} className="flex flex-col gap-1 text-xs" data-testid={`calendar-source-${source.source}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{t(`coverage.sources.${source.source}`)}</span>
                <Badge variant="outline">{t(`coverage.states.${source.state}`)}</Badge>
                {source.from_cache ? <Badge variant="freshOk">{t("coverage.cached")}</Badge> : null}
                {source.used_fallback && !source.from_cache ? <Badge variant="freshOk">{t("coverage.fallback")}</Badge> : null}
              </div>
              {source.state !== "not_configured" ? (
                <span className="text-muted-foreground">{displayProvider(source.provider, i18n.language)}</span>
              ) : null}
              {source.checked_at ? (
                <span className="tabular-nums text-muted-foreground">{t("coverage.checkedAt", { time: formatDateTime(source.checked_at, i18n.language) })}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
