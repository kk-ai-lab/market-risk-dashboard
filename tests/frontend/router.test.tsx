/**
 * Routing / language switch / theme switch tests (acceptance 2/3/4).
 * - 8 routes renderable (fixtures data)
 * - language switch keeps the current page (#/zh/risk-lab → #/en/risk-lab)
 * - language persists after refresh (localStorage + URL segment)
 * - dark/light mode switch + localStorage persistence
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/i18n";
import App from "@/App";
import { LOCALE_STORAGE_KEY } from "@/i18n";
import { THEME_STORAGE_KEY } from "@/hooks/useTheme";
import { DashboardEnvelope, RiskEnvelope } from "@/schemas/generated/contracts";
import { FIXTURE_MAP, fixtureForUrl, installCustomFetch, installFixtureFetch, installFailingFetch } from "./helpers/fetchMock";

// CI runs all 12 test files in parallel; lazy-loaded routes + TanStack Query
// can exceed the default 1s findBy timeout under CPU contention.
configure({ asyncUtilTimeout: 5000 });

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const renderResult = render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
  return { ...renderResult, queryClient };
}

function setHash(hash: string): void {
  window.history.replaceState(null, "", hash);
}

beforeEach(() => {
  window.localStorage.clear();
  setHash("#/zh/overview");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("route rendering (fixtures data)", () => {
  it.each(["zh", "en"])("discloses evidence and proxy drivers on the overview in %s", async (locale) => {
    const dashboard = DashboardEnvelope.parse(FIXTURE_MAP["/latest/dashboard.json"]);
    dashboard.payload.risk.evidence_coverage = 0.65;
    dashboard.payload.risk.score_lower_bound = 40;
    dashboard.payload.risk.score_upper_bound = 70;
    installCustomFetch((url) => url.endsWith("/latest/dashboard.json")
      ? { ok: true, body: dashboard } : fixtureForUrl(url));
    setHash(`#/${locale}/overview`);
    renderApp();
    const evidence = await screen.findByTestId("overview-risk-evidence");
    expect(evidence).toHaveTextContent(locale === "zh" ? "证据不完整" : "Partial evidence");
    expect(evidence).toHaveTextContent("65%");
    expect(evidence).toHaveTextContent("40–70");
    expect(within(screen.getByTestId("top-drivers")).getByText(locale === "zh" ? "代理" : "Proxy")).toBeInTheDocument();
  });

  it.each(["zh", "en"])("does not invent missing evidence or bounds on the overview in %s", async (locale) => {
    const dashboard = DashboardEnvelope.parse(FIXTURE_MAP["/latest/dashboard.json"]);
    dashboard.payload.risk.evidence_state = null;
    dashboard.payload.risk.evidence_coverage = null;
    dashboard.payload.risk.score_lower_bound = null;
    dashboard.payload.risk.score_upper_bound = null;
    installCustomFetch((url) => url.endsWith("/latest/dashboard.json")
      ? { ok: true, body: dashboard } : fixtureForUrl(url));
    setHash(`#/${locale}/overview`);
    renderApp();
    const evidence = await screen.findByTestId("overview-risk-evidence");
    expect(evidence).toHaveTextContent(locale === "zh" ? "证据状态未知" : "Evidence status unknown");
    expect(within(evidence).getAllByText("—")).toHaveLength(2);
    expect(evidence).not.toHaveTextContent("52.3–52.3");
  });

  it.each(["zh", "en"])("does not display a placeholder zero score when all evidence is absent in %s", async (locale) => {
    const risk = RiskEnvelope.parse(FIXTURE_MAP["/latest/risk.json"]);
    risk.payload.total_score = 0;
    risk.payload.risk_level = "risk_on";
    risk.payload.dimensions = risk.payload.dimensions.map((dimension) => ({
      ...dimension,
      score: 0,
      coverage: 0,
      indicators: dimension.indicators.map((indicator) => ({ ...indicator, value: null, risk_score: 50 })),
    }));
    const dashboard = DashboardEnvelope.parse(FIXTURE_MAP["/latest/dashboard.json"]);
    dashboard.payload.risk = risk.payload;
    installCustomFetch((url) => url.endsWith("/latest/risk.json") ? { ok: true, body: risk }
      : url.endsWith("/latest/dashboard.json") ? { ok: true, body: dashboard } : fixtureForUrl(url));
    setHash(`#/${locale}/overview`);
    renderApp();
    await waitFor(() => expect(screen.getByTestId("page-title")).toHaveTextContent(locale === "zh" ? "总览" : "Overview"));
    expect(await screen.findByTestId("risk-score")).toHaveTextContent("—");
    expect(screen.getByTestId("risk-level")).toHaveTextContent(locale === "zh" ? "证据不足" : "Insufficient evidence");
    const riskLink = document.querySelector(`a[href="#/${locale}/risklab"]`);
    expect(riskLink).not.toBeNull();
    fireEvent.click(riskLink!);
    await waitFor(() => expect(window.location.hash).toBe(`#/${locale}/risklab`));
    await waitFor(() => expect(screen.getByTestId("page-title")).toHaveTextContent(locale === "zh" ? "风险实验室" : "Risk Lab"));
    expect(await screen.findByTestId("risk-score")).toHaveTextContent("—");
    expect(screen.getByTestId("risk-level")).toHaveTextContent(locale === "zh" ? "证据不足" : "Insufficient evidence");
    expect(screen.getAllByTestId("risk-indicator")[0]).not.toHaveTextContent("50");
  });

  it("retains a real zero risk score when evidence exists", async () => {
    const dashboard = DashboardEnvelope.parse(FIXTURE_MAP["/latest/dashboard.json"]);
    dashboard.payload.risk.total_score = 0;
    dashboard.payload.risk.risk_level = "risk_on";
    installCustomFetch((url) => url.endsWith("/latest/dashboard.json")
      ? { ok: true, body: dashboard } : fixtureForUrl(url));
    renderApp();
    expect(await screen.findByTestId("risk-score")).toHaveTextContent("0");
    expect(screen.getByTestId("risk-level")).toHaveTextContent("风险偏好");
  });

  it.each([["zh", "证据状态未知"], ["en", "Evidence status unknown"]])(
    "does not present a legacy score as verified when evidence status is unknown in %s",
    async (locale, label) => {
      const dashboard = DashboardEnvelope.parse(FIXTURE_MAP["/latest/dashboard.json"]);
      dashboard.payload.risk.evidence_state = null;
      installCustomFetch((url) => url.endsWith("/latest/dashboard.json")
        ? { ok: true, body: dashboard } : fixtureForUrl(url));
      setHash(`#/${locale}/overview`);
      renderApp();
      expect(await screen.findByTestId("risk-score")).toHaveTextContent("—");
      expect(screen.getByTestId("risk-level")).toHaveTextContent(label);
    },
  );

  it.each(["zh", "en"])("hides the aggregate score when coverage is insufficient in %s", async (locale) => {
    const risk = RiskEnvelope.parse(FIXTURE_MAP["/latest/risk.json"]);
    risk.payload.evidence_state = "insufficient_evidence";
    const dashboard = DashboardEnvelope.parse(FIXTURE_MAP["/latest/dashboard.json"]);
    dashboard.payload.risk = risk.payload;
    installCustomFetch((url) => url.endsWith("/latest/risk.json") ? { ok: true, body: risk }
      : url.endsWith("/latest/dashboard.json") ? { ok: true, body: dashboard } : fixtureForUrl(url));
    setHash(`#/${locale}/overview`);
    renderApp();
    expect(await screen.findByTestId("risk-score")).toHaveTextContent("—");
    expect(screen.getByTestId("risk-level")).toHaveTextContent(locale === "zh" ? "证据不足" : "Insufficient evidence");
    fireEvent.click(document.querySelector(`a[href="#/${locale}/risklab"]`)!);
    await waitFor(() => expect(window.location.hash).toBe(`#/${locale}/risklab`));
    await waitFor(() => expect(screen.getByTestId("page-title")).toHaveTextContent(locale === "zh" ? "风险实验室" : "Risk Lab"));
    expect(await screen.findByTestId("risk-score")).toHaveTextContent("—");
  });

  it("shows the breadth sample alongside its proxy label", async () => {
    installFixtureFetch();
    setHash("#/zh/risklab");
    renderApp();
    const sample = await screen.findByTestId("breadth-sample");
    expect(sample).toHaveTextContent("10 / 18");
    expect(sample).toHaveTextContent("代理");
    expect(sample).toHaveTextContent("不代表整个市场");
  });

  it("overview renders market data and risk without generated analysis", async () => {
    const metrics = installFixtureFetch();
    renderApp();
    await screen.findByTestId("page-title");
    await waitFor(() => expect(screen.getByTestId("page-title")).toHaveTextContent("总览"));
    expect(await screen.findByTestId("risk-score")).toHaveTextContent("52.3");
    expect(screen.getByTestId("risk-level")).toHaveTextContent("谨慎");
    expect(screen.getByTestId("market-regime")).toHaveTextContent("周期末段");
    expect(screen.getByText("跨资产确认信号")).toBeInTheDocument();
    expect(screen.getAllByText("宏观").length).toBeGreaterThan(0);
    // #121: 板块表现 lists all sector baskets (sectors + themes merged), not just the 2 headline sectors.
    const sectorPerformance = await screen.findByTestId("sector-performance");
    expect(within(sectorPerformance).getByText("半导体龙头")).toBeInTheDocument(); // sectors.semis
    expect(within(sectorPerformance).getByText("存储")).toBeInTheDocument(); // themes.memory
    expect(within(sectorPerformance).getByText("网络安全")).toBeInTheDocument(); // themes.cybersecurity
    expect(screen.queryByTestId("ai-brief")).not.toBeInTheDocument();

    const dataRequests = metrics.requests.filter((url) => url.includes("/data/"));
    expect(dataRequests).toHaveLength(4);
    expect(dataRequests.some((url) => /\/(?:analysis\.[^/]+|facts)\.json$/.test(url))).toBe(false);
    expect(dataRequests.filter((url) => url.endsWith("/latest/dashboard.json"))).toHaveLength(1);
    for (const removedDataset of ["crypto", "equities", "sectors", "calendar", "risk.json"]) {
      expect(dataRequests.some((url) => url.endsWith(`/latest/${removedDataset}.json`))).toBe(false);
    }
    const previousFanout = [
      "/latest/risk.json",
      "/latest/macro.json",
      "/latest/crypto.json",
      "/latest/equities.json",
      "/latest/sectors.json",
      "/latest/calendar.json",
      "/latest/news.json",
      "/history/risk/30d.json",
      "/latest/analysis.zh-CN.json",
      "/latest/analysis.en.json",
      "/latest/facts.json",
    ].reduce((bytes, suffix) => bytes + JSON.stringify(FIXTURE_MAP[suffix]).length, 0);
    expect(metrics.responseBytes).toBeLessThan(previousFanout);
  });

  it.each(["en", "zh"])("shows configured index and SPDR rows with source and proxy details in %s", async (locale) => {
    const dashboard = structuredClone(FIXTURE_MAP["/latest/dashboard.json"]) as {
      payload: {
        cross_asset: Array<Record<string, unknown>>;
        index_performance: Array<Record<string, unknown>>;
        sector_etfs: Array<Record<string, unknown>>;
      };
    };
    const indexSymbols = ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"];
    const sectorSymbols = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"];
    dashboard.payload.index_performance = indexSymbols.map((symbol, index) => ({
      key: `index_${index}`,
      symbol: index === 0 ? "SPY" : symbol,
      change_1d: 0.5,
      source: "Yahoo Finance",
      provider: "yfinance",
      retrieved_at: "2026-11-23T12:00:00Z",
      is_proxy: index === 0,
      proxy_of: index === 0 ? "^GSPC" : null,
    }));
    dashboard.payload.sector_etfs = sectorSymbols.map((symbol) => ({
      key: symbol,
      symbol,
      change_1d: -0.25,
      source: "Yahoo Finance",
      provider: "yfinance",
      retrieved_at: "2026-11-23T12:00:00Z",
      is_proxy: false,
      proxy_of: null,
    }));
    installCustomFetch((url) => url.endsWith("/latest/dashboard.json")
      ? { ok: true, body: dashboard } : fixtureForUrl(url));
    setHash(`#/${locale}/overview`);
    renderApp();

    const heatmap = await screen.findByTestId("asset-heatmap");
    expect(within(heatmap).getAllByTestId("heatmap-cell")).toHaveLength(
      dashboard.payload.cross_asset.length
        + dashboard.payload.index_performance.length
        + dashboard.payload.sector_etfs.length,
    );
    expect(within(heatmap).getAllByText(locale === "en" ? "SPDR sector ETFs" : "板块交易型基金").length).toBeGreaterThan(0);
    expect(within(heatmap).getAllByText(locale === "en" ? "Proxy for ^GSPC" : "^GSPC 的代理指标").length).toBeGreaterThan(0);
    expect(within(heatmap).getAllByText("Yahoo Finance · yfinance").length).toBeGreaterThanOrEqual(16);
    const categoryGrids = within(heatmap).getAllByTestId(/^heatmap-category-grid-/);
    const retrievedRows = categoryGrids.flatMap((grid) =>
      within(grid).queryAllByText(locale === "en" ? /^Retrieved / : /^拉取时间：/),
    );
    expect(retrievedRows).toHaveLength(16);
  });

  it("attributes Coin Metrics crypto reference rates shown by the overview", async () => {
    const dashboard = structuredClone(FIXTURE_MAP["/latest/dashboard.json"]) as {
      payload: { cross_asset: Array<{ category: string; source?: string }> };
    };
    const bitcoin = dashboard.payload.cross_asset.find((asset) => asset.category === "crypto");
    expect(bitcoin).toBeDefined();
    bitcoin!.source = "coinmetrics";
    installCustomFetch((url) => url.endsWith("/latest/dashboard.json")
      ? { ok: true, body: dashboard }
      : fixtureForUrl(url));

    renderApp();
    expect(await screen.findByTestId("coinmetrics-attribution")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CC BY-NC 4.0" })).toHaveAttribute(
      "href", "https://creativecommons.org/licenses/by-nc/4.0/",
    );
  });

  it("keeps dashboard and targeted datasets in distinct stable query identities", async () => {
    installFixtureFetch();
    const { queryClient } = renderApp();
    await screen.findByTestId("risk-score");

    const queryKeys = queryClient.getQueryCache().getAll().map((query) => query.queryKey);
    expect(queryKeys).toContainEqual(["dashboard", "none", "latest", "default"]);
    expect(queryKeys).toContainEqual(["risk", "none", "30d", "custom"]);
    expect(new Set(queryKeys.map((key) => JSON.stringify(key))).size).toBe(queryKeys.length);
  });

  it("renders the homepage in the specified read-model section order", async () => {
    installFixtureFetch();
    const { container } = renderApp();
    await screen.findByTestId("risk-score");

    const sectionIds = [
      "risk-conclusion",
      "risk-trend-section",
      "cross-asset-section",
      "drivers-catalysts-section",
      "sectors-news-section",
    ];
    const positions = sectionIds.map((id) => {
      const element = container.querySelector(`[data-testid="${id}"]`);
      expect(element).not.toBeNull();
      return [...container.querySelectorAll("*")].indexOf(element as Element);
    });
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("shows dashboard schema failure and recovers through retry", async () => {
    let dashboardAttempts = 0;
    installCustomFetch((url) => {
      if (url.endsWith("/latest/dashboard.json")) {
        dashboardAttempts += 1;
        if (dashboardAttempts < 3) return { ok: true, body: { invalid: true } };
      }
      return fixtureForUrl(url);
    });
    renderApp();

    const conclusion = await screen.findByTestId("risk-conclusion");
    expect(await within(conclusion).findByRole("alert")).toBeInTheDocument();
    fireEvent.click(await within(conclusion).findByRole("button", { name: "重试" }));
    expect(await screen.findByTestId("risk-score")).toHaveTextContent("52.3");
    expect(dashboardAttempts).toBe(3);
  });

  it("keeps degraded dashboard freshness visible while rendering valid partial data", async () => {
    installCustomFetch((url) => {
      const fixture = fixtureForUrl(url);
      if (url.endsWith("/latest/dashboard.json") && fixture.ok) {
        return {
          ok: true,
          body: {
            ...(fixture.body as Record<string, unknown>),
            freshness_status: "degraded",
          },
        };
      }
      return fixture;
    });
    renderApp();
    expect(await screen.findByTestId("risk-score")).toHaveTextContent("52.3");
    expect(screen.getByTestId("status-badge-degraded")).toBeInTheDocument();
  });

  it("keeps homepage ordinary text at the readable minimum", async () => {
    installFixtureFetch();
    renderApp();
    await screen.findByTestId("risk-score");
    const undersized = [...document.querySelectorAll<HTMLElement>("[class]")].filter((element) =>
      /\btext-\[(?:9|10|11)px\]/.test(element.className),
    );
    expect(undersized).toHaveLength(0);
  });

  it("keeps homepage landmarks, status meaning, and controls accessible", async () => {
    installFixtureFetch();
    renderApp();
    await screen.findByTestId("risk-score");

    expect(screen.getAllByRole("banner").length).toBeGreaterThan(0);
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getAllByRole("contentinfo").length).toBeGreaterThan(0);
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "主题" })).toBeInTheDocument();
    expect(screen.getAllByTestId(/^status-badge-/).every((badge) => badge.textContent?.trim())).toBe(true);

    const languageSwitch = screen.getByRole("button", { name: "切换语言" });
    languageSwitch.focus();
    expect(document.activeElement).toBe(languageSwitch);
  });

  it("overview keeps market data and risk available on both locale routes", async () => {
    installFixtureFetch();
    renderApp();
    expect(await screen.findByTestId("risk-score")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-brief-section")).not.toBeInTheDocument();
    expect(screen.queryByText("智能市场简报")).not.toBeInTheDocument();
    expect(screen.getAllByText("主要风险驱动").length).toBeGreaterThan(0);
    expect(screen.getByText("英文")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("Top 风险驱动");
    expect(document.body.textContent).not.toContain("This indicator is a modeled estimate");
    expect(document.body.textContent).not.toContain("1M ");

    fireEvent.click(screen.getByTestId("lang-switch"));

    await waitFor(() => expect(window.location.hash).toBe("#/en/overview"));
    expect(await screen.findByTestId("risk-score")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-brief-section")).not.toBeInTheDocument();
    expect(screen.queryByText("AI Market Brief")).not.toBeInTheDocument();
  });

  it.each([
    ["macro", "宏观"],
    ["equities", "股票"],
    ["themes", "板块"],
    ["news", "官方公告"],
    ["calendar", "日历"],
    ["risklab", "风险实验室"],
    ["status", "系统状态"],
  ])("page %s renders (fixtures)", async (page, title) => {
    installFixtureFetch();
    setHash(`#/zh/${page}`);
    renderApp();
    expect(await screen.findByTestId("page-title")).toHaveTextContent(title);
  });

  it("shows only announcements from the approved Fed and SEC feeds", async () => {
    const news = structuredClone(FIXTURE_MAP["/latest/news.json"]) as {
      payload: { items: Array<Record<string, unknown>>; total: number };
    };
    const official = news.payload.items[0];
    news.payload.items.push(
      {
        ...official,
        id: "unapproved-cnbc",
        title: "Unapproved CNBC headline",
        source: "CNBC",
        source_id: "cnbc",
      },
      {
        ...official,
        id: "legacy-unidentified",
        title: "Legacy unidentified headline",
        source: "Unknown source",
        source_id: null,
      },
    );
    news.payload.total = news.payload.items.length;
    installCustomFetch((url) => url.endsWith("/latest/news.json")
      ? { ok: true, body: news }
      : fixtureForUrl(url));
    setHash("#/en/news");
    renderApp();

    expect(await screen.findByText("Federal Reserve releases corrected minutes")).toBeInTheDocument();
    expect(screen.queryByText("Unapproved CNBC headline")).not.toBeInTheDocument();
    expect(screen.queryByText("Legacy unidentified headline")).not.toBeInTheDocument();
    expect(screen.getByText("Total news: 1")).toBeInTheDocument();
  });

  it("invalid language segment → redirects to /en/overview", async () => {
    installFixtureFetch();
    setHash("#/fr/overview");
    renderApp();
    await waitFor(() => expect(window.location.hash).toContain("/en/overview"));
  });
});

describe("JSON read failure → ErrorState", () => {
  it("renders ErrorState when data is unreachable (does not crash)", async () => {
    installFailingFetch();
    renderApp();
    const alerts = await screen.findAllByRole("alert", {}, { timeout: 5000 });
    expect(alerts.length).toBeGreaterThan(0);
    expect(screen.getAllByText("数据加载失败").length).toBeGreaterThan(0);
  });
});

describe("language switch (keeps current page)", () => {
  it("stays on risk-lab when switching zh→en on the risk-lab page", async () => {
    installFixtureFetch();
    setHash("#/zh/risklab");
    renderApp();
    expect(await screen.findByTestId("page-title")).toHaveTextContent("风险实验室");
    expect(await screen.findByTestId("risk-evidence-state")).toHaveTextContent("证据不完整");
    expect(await screen.findByTestId("risk-calibration-policy")).toHaveTextContent("暂定校准");
    expect(await screen.findByTestId("cross-asset-signals")).toHaveTextContent("周期股相对防御股");
    expect(screen.getByTestId("cross-asset-signals").textContent).not.toContain("yfinance");
    expect(document.body.textContent).not.toContain("This indicator is a modeled estimate");
    expect(document.body.textContent).not.toContain("10Y");

    fireEvent.click(screen.getByTestId("lang-switch"));

    await waitFor(() => expect(window.location.hash).toBe("#/en/risklab"));
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    await waitFor(() => expect(screen.getByTestId("page-title")).toHaveTextContent("Risk Lab"));
    expect(await screen.findByTestId("risk-evidence-state")).toHaveTextContent("Partial evidence");
  });

  it("stays on overview when switching en→zh on the overview page", async () => {
    installFixtureFetch();
    setHash("#/en/overview");
    renderApp();
    await screen.findByTestId("page-title");
    await waitFor(() => expect(screen.getByTestId("page-title")).toHaveTextContent("Overview"));

    fireEvent.click(screen.getByTestId("lang-switch"));

    await waitFor(() => expect(window.location.hash).toBe("#/zh/overview"));
    await waitFor(() => expect(screen.getByTestId("page-title")).toHaveTextContent("总览"));
  });

  it("switches language when localStorage is unavailable", async () => {
    installFixtureFetch();
    setHash("#/en/overview");
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    renderApp();
    await screen.findByTestId("page-title");

    fireEvent.click(screen.getByTestId("lang-switch"));

    await waitFor(() => expect(window.location.hash).toBe("#/zh/overview"));
    expect(setItem).toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("keeps the news page usable when sessionStorage is unavailable", async () => {
    installFixtureFetch();
    setHash("#/en/news");
    const getItem = vi.spyOn(window.sessionStorage, "getItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    const setItem = vi.spyOn(window.sessionStorage, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });

    renderApp();

    await expect(screen.findByTestId("news-filter")).resolves.toBeInTheDocument();
    getItem.mockRestore();
    setItem.mockRestore();
  });

  it("language persists after refresh (URL segment + localStorage)", async () => {
    installFixtureFetch();
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "en");
    setHash("#/en/macro");
    renderApp();
    await screen.findByTestId("page-title");
    await waitFor(() => expect(screen.getByTestId("page-title")).toHaveTextContent("Macro"));
  });
});

describe("theme switch", () => {
  it("dark by default → three-way control switches preference → localStorage persists", async () => {
    installFixtureFetch();
    renderApp();
    await screen.findByTestId("page-title");

    expect(document.documentElement.classList.contains("dark")).toBe(true);

    fireEvent.click(screen.getByTestId("theme-option-light"));
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    fireEvent.click(screen.getByTestId("theme-option-dark"));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });
});
