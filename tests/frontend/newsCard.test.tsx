/**
 * NewsCard bilingual selection tests (issue #37).
 * Canonical bilingual (ADR-0003): title/summary are English, title_zh/summary_zh Chinese.
 * Selection: zh-CN locale prefers the _zh field, en prefers the English field; missing
 * or foreign-language prose fails closed to a localized placeholder.
 */
import { describe, expect, it, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { NewsCard } from "@/components/news/NewsCard";
import { NewsItem as NewsItemSchema } from "@/schemas";
import type { NewsItem } from "@/schemas";
import originals from "../fixtures/news-originals.json";

function makeItem(overrides: Partial<NewsItem> = {}): NewsItem {
  return {
    id: "item-1",
    title: "Fed raises rates by 25bp",
    title_zh: "美联储加息25个基点",
    lang: "en",
    source: "Federal Reserve",
    url: "https://example.com/news/1",
    published_at: "2026-08-03T10:00:00Z",
    categories: ["macro"],
    assets: [],
    importance: 70,
    sentiment: "neutral",
    summary: "The Fed raised its target range by 25 basis points.",
    summary_zh: "美联储将目标区间上调25个基点。",
    impact_window: null,
    ...overrides,
  };
}

async function setLocale(locale: string) {
  await i18n.changeLanguage(locale);
}

describe("NewsCard bilingual selection (canonical bilingual, ADR-0003)", () => {
  // No global locale setup: each test awaits its own setLocale so changeLanguage calls cannot
  // resolve out of order (an un-awaited beforeEach call made tests race-prone).
  afterEach(() => {
    cleanup();
  });

  it("en locale shows English title and summary", async () => {
    await setLocale("en");
    render(<NewsCard item={makeItem()} />);
    expect(screen.getByText("Fed raises rates by 25bp")).toBeInTheDocument();
    expect(screen.getByText("The Fed raised its target range by 25 basis points.")).toBeInTheDocument();
    expect(screen.queryByText("美联储加息25个基点")).not.toBeInTheDocument();
  });

  it("zh-CN locale shows Chinese title and summary", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem()} />);
    expect(screen.getByText("美联储加息25个基点")).toBeInTheDocument();
    expect(screen.getByText("美联储将目标区间上调25个基点。")).toBeInTheDocument();
    expect(screen.queryByText("Fed raises rates by 25bp")).not.toBeInTheDocument();
  });

  it("zh-CN locale uses a localized placeholder when translation is missing", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem({ title_zh: null, summary_zh: null })} />);
    expect(screen.getByText("暂无本语言翻译")).toBeInTheDocument();
    expect(screen.queryByText("Fed raises rates by 25bp")).not.toBeInTheDocument();
  });

  it("shows collected Chinese originals with company names in Chinese locale", async () => {
    await setLocale("zh-CN");
    const item = NewsItemSchema.parse({ ...makeItem(), ...originals[0] });
    render(<NewsCard item={item} />);
    expect(screen.getByText(originals[0].title)).toBeInTheDocument();
    expect(screen.getByText(originals[0].summary)).toBeInTheDocument();
    expect(screen.queryByText("暂无本语言翻译")).not.toBeInTheDocument();
  });

  it("does not present Chinese-source originals as an English translation", async () => {
    await setLocale("en");
    const item = NewsItemSchema.parse({ ...makeItem(), ...originals[0] });
    render(<NewsCard item={item} />);
    expect(screen.getAllByText("Translation unavailable")).toHaveLength(2);
    expect(screen.queryByText(originals[0].title)).not.toBeInTheDocument();
    expect(screen.queryByText(originals[0].summary)).not.toBeInTheDocument();
  });

  it("keeps untranslated English-source news unavailable in Chinese locale", async () => {
    await setLocale("zh-CN");
    const item = NewsItemSchema.parse({ ...makeItem(), ...originals[1] });
    render(<NewsCard item={item} />);
    expect(screen.getByText("暂无本语言翻译")).toBeInTheDocument();
    expect(screen.queryByText(originals[1].title)).not.toBeInTheDocument();
    expect(screen.queryByText(originals[1].summary)).not.toBeInTheDocument();
  });

  it("does not infer an English translation from Latin text in a Chinese source", async () => {
    await setLocale("en");
    render(<NewsCard item={makeItem({ lang: "zh", title: "OpenAI", title_zh: null, summary: "", summary_zh: null })} />);
    expect(screen.getByText("Translation unavailable")).toBeInTheDocument();
    expect(screen.queryByText("OpenAI")).not.toBeInTheDocument();
  });

  it("en locale uses a localized placeholder when only Chinese exists", async () => {
    await setLocale("en");
    render(
      <NewsCard
        item={makeItem({
          lang: "zh",
          // Source-language text is not an English translation.
          title: "全球市场收跌",
          title_zh: null,
          summary: "美股三大指数收跌",
          summary_zh: null,
        })}
      />
    );
    expect(screen.getAllByText("Translation unavailable").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("全球市场收跌")).not.toBeInTheDocument();
  });

  it("does not render a summary block when no summary exists in any language", async () => {
    await setLocale("en");
    render(<NewsCard item={makeItem({ summary: "", summary_zh: null })} />);
    expect(screen.getByText("Fed raises rates by 25bp")).toBeInTheDocument();
    expect(screen.queryByText("The Fed raised its target range by 25 basis points.")).not.toBeInTheDocument();
  });
});
