import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import OverviewPage from "@/pages/OverviewPage";
import { fixtureForUrl, installCustomFetch, installFixtureFetch } from "./helpers/fetchMock";
import { macroFixture, newsFixture } from "./helpers/fixtureData";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/en/overview"]}>
        <OverviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
  installFixtureFetch();
});

afterEach(() => {
  cleanup();
});

describe("OverviewPage credit indicators", () => {
  it("matches Baa, HY, and IG by exact series key and shows attribution", async () => {
    renderPage();

    const baa = await screen.findByTestId("credit-baa10y");
    await screen.findByText("3.42");
    const hy = screen.getByTestId("credit-hy-oas");
    const ig = screen.getByTestId("credit-ig-oas");

    expect(baa).toHaveTextContent("3.42%");
    expect(within(baa).getByText("Moody's via FRED")).toBeInTheDocument();
    expect(baa).toHaveTextContent("stands in for HY OAS");
    expect(baa).toHaveTextContent("As of 2026-08-03");
    expect(baa).not.toHaveTextContent("3.21%");

    expect(hy).toHaveTextContent("3.21%");
    expect(hy).toHaveTextContent("Shown for context; not a risk-score input");
    expect(within(hy).getByRole("img", { name: "Full data citation" })).toHaveAttribute(
      "title",
      expect.stringContaining("BAMLH0A0HYM2"),
    );

    expect(ig).toHaveTextContent("0.91%");
    expect(within(ig).getByRole("img", { name: "Full data citation" })).toHaveAttribute(
      "title",
      expect.stringContaining("BAMLC0A0CM"),
    );
  });

  it("hides empty credit cards and links to the source status", async () => {
    const macro = structuredClone(macroFixture);
    macro.payload.credit = [];
    installCustomFetch((url) => {
      if (url.endsWith("/latest/macro.json")) return { ok: true, body: macro };
      return fixtureForUrl(url);
    });

    renderPage();

    expect(await screen.findByTestId("credit-indicators-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("credit-indicators-unavailable")).toHaveTextContent("Credit spread data is temporarily unavailable.");
    expect(screen.queryByTestId("credit-indicators")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View data status" })).toHaveAttribute("href", "/en/status");
  });

  it("shows only announcements from the approved Fed and SEC feeds", async () => {
    const news = structuredClone(newsFixture) as {
      payload: { items: Array<Record<string, unknown>>; total: number };
    };
    const official = news.payload.items[0];
    news.payload.items.push(
      {
        ...official,
        id: "overview-unapproved-cnbc",
        title: "Unapproved CNBC overview headline",
        source: "CNBC",
        source_id: "cnbc",
      },
      {
        ...official,
        id: "overview-legacy-unidentified",
        title: "Legacy unidentified overview headline",
        source: "Unknown source",
        source_id: null,
      },
    );
    news.payload.total = news.payload.items.length;
    installCustomFetch((url) => url.endsWith("/latest/news.json")
      ? { ok: true, body: news }
      : fixtureForUrl(url));

    renderPage();

    const section = await screen.findByTestId("important-news");
    expect(within(section).getByText("Federal Reserve releases corrected minutes")).toBeInTheDocument();
    expect(within(section).queryByText("Unapproved CNBC overview headline")).not.toBeInTheDocument();
    expect(within(section).queryByText("Legacy unidentified overview headline")).not.toBeInTheDocument();
  });
});
