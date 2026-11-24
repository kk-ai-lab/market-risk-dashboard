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
    source_id: overrides.source_id ?? null,
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

  it("keeps mixed Chinese and Latin text in a Chinese translation", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem({ title_zh: "苹果 Apple 发布财报", summary_zh: "苹果 Apple 公布季度业绩。" })} />);
    expect(screen.getByText("苹果 Apple 发布财报")).toHaveAttribute("lang", "zh-CN");
    expect(screen.getByText("苹果 Apple 公布季度业绩。")).toHaveAttribute("lang", "zh-CN");
    expect(screen.queryByTestId("news-source-language")).not.toBeInTheDocument();
  });

  it("zh-CN locale shows English source text with a language label when translation is missing", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem({ title_zh: null, summary_zh: null })} />);
    expect(screen.getByText("英文原文")).toBeInTheDocument();
    expect(screen.getByText("Fed raises rates by 25bp")).toHaveAttribute("lang", "en");
    expect(screen.getByText("The Fed raised its target range by 25 basis points.")).toHaveAttribute("lang", "en");
    expect(screen.queryByText("暂无本语言翻译")).not.toBeInTheDocument();
  });

  it("shows the original-language label for only the fields missing a safe Chinese translation", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem({ title_zh: "美联储加息25个基点", summary_zh: "   " })} />);
    expect(screen.getByText("美联储加息25个基点")).toHaveAttribute("lang", "zh-CN");
    expect(screen.getByText("The Fed raised its target range by 25 basis points.")).toHaveAttribute("lang", "en");
    expect(screen.getByText("英文原文")).toBeInTheDocument();
  });

  it("does not label an empty summary as an English fallback", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem({ title_zh: "美联储加息25个基点", summary: "   ", summary_zh: "   " })} />);
    expect(screen.getByText("美联储加息25个基点")).toBeInTheDocument();
    expect(screen.queryByText("英文原文")).not.toBeInTheDocument();
    expect(screen.queryByText("The Fed raised its target range by 25 basis points.")).not.toBeInTheDocument();
  });

  it("treats unsafe translated text as missing and falls back to the labeled English source", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem({ title_zh: "Fed raised rates by 25bp" })} />);
    expect(screen.getByText("英文原文")).toBeInTheDocument();
    expect(screen.getByText("Fed raises rates by 25bp")).toHaveAttribute("lang", "en");
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

  it("shows the English fields for translated Chinese-source news in English locale", async () => {
    await setLocale("en");
    render(<NewsCard item={makeItem({
      lang: "zh",
      title: "Fed raises rates by 25bp",
      title_zh: "美联储加息25个基点",
      summary: "The Fed raised its target range by 25 basis points.",
      summary_zh: "美联储将目标区间上调25个基点。",
    })} />);
    expect(screen.getByText("Fed raises rates by 25bp")).toHaveAttribute("lang", "en");
    expect(screen.getByText("The Fed raised its target range by 25 basis points.")).toHaveAttribute("lang", "en");
    expect(screen.queryByText("Translation unavailable")).not.toBeInTheDocument();
  });

  it("keeps canonical English for Chinese-source news with a mixed-script Chinese translation", async () => {
    await setLocale("en");
    render(<NewsCard item={makeItem({
      lang: "zh",
      title: "Apple reports quarterly earnings",
      title_zh: "苹果 Apple 发布财报",
      summary: "Apple reported quarterly results.",
      summary_zh: "苹果 Apple 公布季度业绩。",
    })} />);
    expect(screen.getByText("Apple reports quarterly earnings")).toHaveAttribute("lang", "en");
    expect(screen.getByText("Apple reported quarterly results.")).toHaveAttribute("lang", "en");
    expect(screen.queryByText("Translation unavailable")).not.toBeInTheDocument();
  });

  it("keeps an English summary when its optional Chinese summary is missing", async () => {
    await setLocale("en");
    render(<NewsCard item={makeItem({
      lang: "zh",
      title: "Apple reports quarterly earnings",
      title_zh: "苹果 Apple 发布财报",
      summary: "Apple reported quarterly results.",
      summary_zh: null,
    })} />);
    expect(screen.getByText("Apple reports quarterly earnings")).toHaveAttribute("lang", "en");
    expect(screen.getByText("Apple reported quarterly results.")).toHaveAttribute("lang", "en");
    expect(screen.queryByText("Translation unavailable")).not.toBeInTheDocument();
  });

  it("does not label an English translation as Chinese when its Chinese field is invalid", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem({ lang: "zh", title: "Fed raises rates by 25bp", title_zh: "invalid English" })} />);
    expect(screen.getByText("暂无本语言翻译")).toHaveAttribute("lang", "zh-CN");
    expect(screen.queryByText("Fed raises rates by 25bp")).not.toBeInTheDocument();
    expect(screen.queryByTestId("news-source-language")).not.toBeInTheDocument();
  });

  it("does not treat a numeric-only field as a Chinese translation", async () => {
    await setLocale("zh-CN");
    render(<NewsCard item={makeItem({ title_zh: "123" })} />);
    expect(screen.getByText("英文原文")).toBeInTheDocument();
    expect(screen.getByText("Fed raises rates by 25bp")).toHaveAttribute("lang", "en");
    expect(screen.queryByText("123")).not.toBeInTheDocument();
  });

  it("shows untranslated English-source news as a labeled original in Chinese locale", async () => {
    await setLocale("zh-CN");
    const item = NewsItemSchema.parse({ ...makeItem(), ...originals[1] });
    render(<NewsCard item={item} />);
    expect(screen.getByText("英文原文")).toBeInTheDocument();
    expect(screen.getByText(originals[1].title)).toHaveAttribute("lang", "en");
    expect(screen.getByText(originals[1].summary)).toHaveAttribute("lang", "en");
    expect(screen.queryByText("暂无本语言翻译")).not.toBeInTheDocument();
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
