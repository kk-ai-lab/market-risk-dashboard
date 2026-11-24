import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, beforeEach } from "vitest";
import "@/i18n";
import { installFixtureFetch } from "./helpers/fetchMock";
import MacroPage from "@/pages/MacroPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MacroPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  installFixtureFetch();
});

afterEach(() => {
  cleanup();
});

describe("MacroPage", () => {
  it("renders populated sections and hides empty groups", async () => {
    renderPage();
    for (const section of ["rates", "credit", "volatility", "inflation", "labor", "liquidity", "fx"]) {
      expect(await screen.findByTestId(`section-${section}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId("section-industry")).not.toBeInTheDocument();
    expect(screen.getAllByText("波动率指数").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("十年期国债收益率").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId("section-fedwatch")).not.toBeInTheDocument();
  });

  it("shows the actual history window beside the selected group", async () => {
    renderPage();
    const historySection = await screen.findByTestId("section-history");
    expect(historySection).toBeInTheDocument();
    expect(historySection).toHaveTextContent("历史走势（90d）");
    expect(await screen.findByTestId("macro-history-fallback")).toBeInTheDocument();
    expect(screen.getByText("DTWEXBGS")).toBeInTheDocument();
    expect(screen.getByText("DEXUSEU")).toBeInTheDocument();
  });

  it("updates the history window label when the group changes", async () => {
    renderPage();
    const historySection = await screen.findByTestId("section-history");
    expect(historySection).toHaveTextContent("历史走势（90d）");
    fireEvent.click(screen.getByRole("button", { name: "利率" }));
    expect(historySection).toHaveTextContent("历史走势（30d）");
  });

  it("switches the history window to 90d", async () => {
    renderPage();
    await screen.findByTestId("macro-history-fallback");
    fireEvent.click(screen.getByRole("button", { name: "90d" }));
    expect(await screen.findByTestId("macro-history-fallback")).toBeInTheDocument();
    expect(screen.getByText("DTWEXBGS")).toBeInTheDocument();
  });
});
