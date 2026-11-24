import { useTranslation } from "react-i18next";
import { ExternalLink, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { ImportanceBadge } from "./ImportanceBadge";
import { formatRelativeTime } from "@/lib/format";
import { displayNewsSource, isDisplayTextSafe } from "@/lib/displayLanguage";
import type { NewsItem } from "@/schemas";

/**
 * NewsCard: single news card (title / source / time / importance / sentiment / related assets).
 * Does not render untrusted HTML (architecture §8.13: AI output is rendered escaped, text only).
 */
export interface NewsCardProps {
  item: NewsItem;
}

type NewsText = {
  text: string;
  lang: "en" | "zh-CN";
  sourceFallback: boolean;
};

const CJK_PATTERN = /[\u3400-\u9fff]/u;

function SentimentIcon({ sentiment }: { sentiment: NewsItem["sentiment"] }) {
  if (sentiment === "positive") return <TrendingUp className="h-3.5 w-3.5 text-risk-low" aria-hidden />;
  if (sentiment === "negative") return <TrendingDown className="h-3.5 w-3.5 text-risk-severe" aria-hidden />;
  return <Minus className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />;
}

export function NewsCard({ item }: NewsCardProps) {
  const { t, i18n } = useTranslation("news");
  const locale = i18n.language;
  const zh = locale.toLowerCase().startsWith("zh");
  const unavailable = t("translationUnavailable");
  function newsText(field: "title" | "summary"): NewsText | null {
    const original = item[field]?.trim() ?? "";
    const translated = (field === "title" ? item.title_zh : item.summary_zh)?.trim() ?? "";
    const hasChineseTranslation = Boolean(translated && CJK_PATTERN.test(translated));
    if (zh) {
      if (hasChineseTranslation) {
        return { text: translated, lang: "zh-CN", sourceFallback: false };
      }
      if (item.lang === "zh" && CJK_PATTERN.test(original)) {
        return { text: original, lang: "zh-CN", sourceFallback: false };
      }
      if (item.lang === "en" && original && isDisplayTextSafe(original, "en")) {
        return { text: original, lang: "en", sourceFallback: true };
      }
      return field === "title" || translated || original
        ? { text: unavailable, lang: "zh-CN", sourceFallback: false }
        : null;
    }
    if (item.lang === "en" && original && isDisplayTextSafe(original, locale)) {
      return { text: original, lang: "en", sourceFallback: false };
    }
    if (item.lang === "zh" && original && isDisplayTextSafe(original, "en")) {
      return { text: original, lang: "en", sourceFallback: false };
    }
    return field === "title" || translated || original
      ? { text: unavailable, lang: "en", sourceFallback: false }
      : null;
  }
  const title = newsText("title");
  const summary = newsText("summary");
  const hasEnglishFallback = Boolean(title?.sourceFallback || summary?.sourceFallback);

  return (
    <Card data-testid="news-card">
      <CardContent className="flex flex-col gap-1.5 p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <ImportanceBadge importance={item.importance} />
            <SentimentIcon sentiment={item.sentiment} />
            <span className="text-xs text-muted-foreground">{formatRelativeTime(item.published_at, locale)}</span>
          </div>
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-[28px] items-center gap-1 text-xs text-primary hover:underline"
            aria-label={t("readMore")}
          >
            {t("readMore")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        </div>
        {title ? <p lang={title.lang} className="break-words text-base font-medium leading-snug text-foreground">{title.text}</p> : null}
        {summary ? <p lang={summary.lang} className="break-words text-base leading-relaxed text-muted-foreground">{summary.text}</p> : null}
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span className="rounded bg-muted px-1.5 py-0.5">{displayNewsSource(item.source, locale)}</span>
          {hasEnglishFallback ? <span className="rounded bg-muted px-1.5 py-0.5" data-testid="news-source-language">{t("sourceLanguageFallback")}</span> : null}
          {item.assets.length > 0 ? (
            <span className="flex items-center gap-1">
              {item.assets.slice(0, 4).map((a) => (
                <span key={a} className="rounded bg-muted px-1.5 py-0.5 font-mono">
                  {a}
                </span>
              ))}
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
