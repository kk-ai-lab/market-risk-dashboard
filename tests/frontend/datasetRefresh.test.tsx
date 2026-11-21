import type { PropsWithChildren } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { useDataset } from "@/hooks/useDataset";
import { DatasetClient } from "@/lib/api";
import type { DatasetKey } from "@/schemas";

const schema = z.object({ revision: z.number() });
let queryClient: QueryClient;

beforeEach(() => {
  vi.useFakeTimers();
  focusManager.setFocused(true);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  focusManager.setFocused(undefined);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

function installVersionedFetch() {
  let revision = 0;
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ revision: ++revision })));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("live page dataset refresh", () => {
  it.each<[DatasetKey, number]>([["dashboard", 5], ["macro", 10], ["calendar", 15]])(
    "refreshes %s after its bounded %i minute window",
    async (key, minutes) => {
      const fetchMock = installVersionedFetch();
      const { result, unmount } = renderHook(() => useDataset<z.infer<typeof schema>>(key, {}, schema), { wrapper });
      await advance(1);
      expect(result.current.data?.revision).toBe(1);
      await advance(minutes * 60_000 - 2);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      await advance(2);
      expect(fetchMock).toHaveBeenCalledTimes(2);
      await advance(1);
      expect(result.current.data?.revision).toBe(2);
      unmount();
      await advance(minutes * 60_000);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    },
  );

  it("pauses background polling and checks stale data on return", async () => {
    const fetchMock = installVersionedFetch();
    const { result } = renderHook(() => useDataset<z.infer<typeof schema>>("dashboard", {}, schema), { wrapper });
    await advance(1);
    focusManager.setFocused(false);
    await advance(15 * 60_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { focusManager.setFocused(true); });
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.data?.revision).toBe(2);
  });

  it("revalidates the same HTTP URL instead of accepting indefinitely cached JSON", async () => {
    const fetchMock = installVersionedFetch();
    const client = new DatasetClient("/");
    await client.fetch("dashboard", {}, schema);
    await client.fetch("dashboard", {}, schema);
    expect(fetchMock.mock.calls).toEqual([
      ["/data/latest/dashboard.json", expect.objectContaining({ cache: "no-cache", signal: expect.any(AbortSignal) })],
      ["/data/latest/dashboard.json", expect.objectContaining({ cache: "no-cache", signal: expect.any(AbortSignal) })],
    ]);
  });
});
