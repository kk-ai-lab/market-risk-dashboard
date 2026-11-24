import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDataset } from "@/hooks/useDataset";
import type { NewsEnvelope } from "@/schemas";
import { NewsList } from "@/components/news/NewsList";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusBadge } from "@/components/layout/StatusBadge";
import { formatNumber } from "@/lib/format";
import { isOfficialAnnouncement } from "@/lib/officialAnnouncements";
import { cn } from "@/lib/utils";

/**
 * NewsPage: news page (top importance list + client-side importance filter).
 */
type NewsFilter = "all" | "high" | "mediumPlus";

const NEWS_FILTERS: NewsFilter[] = ["all", "high", "mediumPlus"];

/** Thresholds mirror ImportanceBadge (high ≥ 70, medium ≥ 40). */
const SESSION_KEY = "mrd:news-filter";

function readSavedFilter(): NewsFilter {
  try {
    const stored = typeof sessionStorage !== "undefined" ? sessionStorage.getItem(SESSION_KEY) : null;
    return stored === "high" || stored === "mediumPlus" ? stored : "all";
  } catch {
    return "all";
  }
}

function storeFilter(filter: NewsFilter): void {
  try {
    sessionStorage.setItem(SESSION_KEY, filter);
  } catch {
    return;
  }
}

export default function NewsPage() {
  const { t, i18n } = useTranslation("news");
  const locale = i18n.language;
  const newsQ = useDataset<NewsEnvelope>("news");

  const [filter, setFilter] = useState<NewsFilter>(readSavedFilter);
  useEffect(() => {
    storeFilter(filter);
  }, [filter]);

  const allItems = (newsQ.data?.payload.items ?? []).filter(isOfficialAnnouncement);
  const filtered = allItems.filter((item) => {
    if (filter === "all") return true;
    if (filter === "high") return item.importance >= 70;
    return item.importance >= 40;
  });
  const visibleFilters = NEWS_FILTERS.filter((key) =>
    key === "all" || allItems.some((item) => key === "high" ? item.importance >= 70 : item.importance >= 40),
  );

  useEffect(() => {
    if (newsQ.data && filter !== "all" && filtered.length === 0) setFilter("all");
  }, [filter, filtered.length, newsQ.data]);

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-lg font-semibold text-foreground" data-testid="page-title">
          {t("title")}
        </h1>
        <p className="text-xs text-muted-foreground">{t("subtitle")}</p>
        <p className="w-full text-xs text-muted-foreground">{t("attribution")}</p>
        {newsQ.data ? (
          <div className="ml-auto flex items-center gap-3">
            <StatusBadge dataset="news" generatedAt={newsQ.data.generated_at} status={newsQ.data.freshness_status} fromCache={newsQ.data.provenance?.from_cache} />
            <span className="text-xs text-muted-foreground">
              {t("total")}: {formatNumber(allItems.length, locale)}
            </span>
          </div>
        ) : null}
      </header>

      {newsQ.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : newsQ.isError ? (
        <ErrorState onRetry={newsQ.refetch} />
      ) : newsQ.data ? (
        <div className="flex flex-col gap-4">
          <div
            className="flex flex-wrap items-center gap-1.5"
            role="group"
            aria-label={t("filter.label")}
            data-testid="news-filter"
          >
            {visibleFilters.map((key) => {
              const active = filter === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setFilter((current) => current === key && key !== "all" ? "all" : key)}
                  aria-pressed={active}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-xs min-h-[28px] transition-[color,background-color,border-color,opacity,transform] duration-motion-quick ease-motion-standard",
                    active
                      ? "border-fresh-ok/40 bg-fresh-ok/10 text-fresh-ok"
                      : "border-hairline text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t(`filter.${key}`)}
                </button>
              );
            })}
          </div>
          {filter !== "all" ? <span className="sr-only" aria-live="polite" aria-atomic="true">{t("filter.resultCount", { count: filtered.length })}</span> : null}
          <div key={filter} className="content-transition">
            <NewsList items={filtered} />
          </div>
        </div>
      ) : (
        <EmptyState title={t("none")} />
      )}
    </div>
  );
}
