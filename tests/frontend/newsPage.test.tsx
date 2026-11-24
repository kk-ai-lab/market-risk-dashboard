import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import NewsPage from "@/pages/NewsPage";
import { FIXTURE_MAP, fixtureForUrl } from "./helpers/fetchMock";

let finishFetch: ((response: Response) => void) | undefined;

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("NewsPage", () => {
  it("keeps the saved filter while its first request is still loading", async () => {
    window.sessionStorage.setItem("mrd:news-filter", "mediumPlus");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/latest/news.json")) {
        return new Promise<Response>((resolve) => { finishFetch = resolve; });
      }
      const { body } = fixtureForUrl(url);
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<QueryClientProvider client={client}><NewsPage /></QueryClientProvider>);
    await waitFor(() => expect(window.sessionStorage.getItem("mrd:news-filter")).toBe("mediumPlus"));
    expect(screen.queryByTestId("news-filter")).not.toBeInTheDocument();

    await act(async () => {
      finishFetch?.(new Response(JSON.stringify(FIXTURE_MAP["/latest/news.json"]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    });

    expect(await screen.findByRole("button", { name: "中及以上" })).toHaveAttribute("aria-pressed", "true");
  });
});
