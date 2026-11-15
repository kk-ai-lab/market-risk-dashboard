import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { DatasetClient } from "@/lib/api";
import { watchPublication } from "@/lib/publication";
import { nodeWebCrypto } from "./helpers/nodeWebCrypto";

const hash = (value: string) => createHash("sha256").update(value).digest("hex");
const firstId = "a".repeat(64);
const secondId = "b".repeat(64);
const schema = z.object({ revision: z.number() });

function generation(id: string, revision: number) {
  const data = JSON.stringify({ revision });
  const manifest = JSON.stringify({ version: 1, generation_id: id, parent_id: null, producer_revision: "revision", created_at: "2026-11-14T00:00:00Z", files: { "latest/risk.json": hash(data), "latest/dashboard.json": hash(data) } });
  const pointer = JSON.stringify({ version: 1, generation_id: id, manifest_sha256: hash(manifest) });
  return { id, data, manifest, pointer };
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("versioned publication session", () => {
  it("pins all requests to the same verified generation after the pointer changes", async () => {
    const first = generation(firstId, 1);
    const second = generation(secondId, 2);
    let current = first;
    const calls: string[] = [];
    vi.stubGlobal("crypto", nodeWebCrypto());
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      if (url === "/site/data/current.json") return new Response(current.pointer);
      if (url === `/site/data/generations/${first.id}/manifest.json`) return new Response(first.manifest);
      if (url.startsWith(`/site/data/generations/${first.id}/latest/`)) return new Response(first.data);
      if (url.startsWith(`/site/data/generations/${second.id}/latest/`)) return new Response(second.data);
      return new Response("not found", { status: 404 });
    }));
    const client = new DatasetClient("/site/", true);
    expect(await client.fetch("risk", {}, schema)).toEqual({ revision: 1 });
    current = second;
    expect(await client.fetch("dashboard", {}, schema)).toEqual({ revision: 1 });
    expect(calls.filter((url) => url.endsWith("current.json"))).toHaveLength(1);
    expect(calls.some((url) => url.includes(secondId))).toBe(false);
  });

  it("rejects corrupt data instead of falling back to a mutable path", async () => {
    const first = generation(firstId, 1);
    const calls: string[] = [];
    vi.stubGlobal("crypto", nodeWebCrypto());
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      if (url.endsWith("current.json")) return new Response(first.pointer);
      if (url.endsWith("manifest.json")) return new Response(first.manifest);
      return new Response(JSON.stringify({ revision: 99 }));
    }));
    await expect(new DatasetClient("/site", true).fetch("risk", {}, schema)).rejects.toThrow("hash mismatch");
    expect(calls).not.toContain("/site/data/latest/risk.json");
  });

  it("rejects a pointer with a bad manifest hash", async () => {
    const first = generation(firstId, 1);
    vi.stubGlobal("crypto", nodeWebCrypto());
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(url.endsWith("current.json") ? first.pointer : "{}")));
    await expect(new DatasetClient("/site", true).fetch("risk", {}, schema)).rejects.toThrow("manifest hash mismatch");
  });

  it("shares the same initial generation across concurrent requests", async () => {
    const first = generation(firstId, 1);
    const calls: string[] = [];
    vi.stubGlobal("crypto", nodeWebCrypto());
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      if (url.endsWith("current.json")) return new Response(first.pointer);
      if (url.endsWith("manifest.json")) return new Response(first.manifest);
      return new Response(first.data);
    }));
    const client = new DatasetClient("/site", true);
    const result = await Promise.all([client.fetch("risk", {}, schema), client.fetch("dashboard", {}, schema)]);
    expect(result).toEqual([{ revision: 1 }, { revision: 1 }]);
    expect(calls.filter((url) => url.endsWith("current.json"))).toHaveLength(1);
    expect(calls.filter((url) => url.endsWith("manifest.json"))).toHaveLength(1);
  });

  it("detects a newer valid publication without mixing the current one", async () => {
    const first = generation(firstId, 1);
    const second = generation(secondId, 2);
    let current = first;
    vi.stubGlobal("crypto", nodeWebCrypto());
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("current.json")) return new Response(current.pointer);
      if (url.includes(first.id) && url.endsWith("manifest.json")) return new Response(first.manifest);
      if (url.includes(second.id) && url.endsWith("manifest.json")) return new Response(second.manifest);
      if (url.includes(first.id)) return new Response(first.data);
      if (url.includes(second.id)) return new Response(second.data);
      return new Response("not found", { status: 404 });
    }));
    const client = new DatasetClient("/site", true);
    await client.fetch("risk", {}, schema);
    expect(await client.hasPublicationUpdate()).toBe(false);
    current = second;
    expect(await client.hasPublicationUpdate()).toBe(true);
    expect(await client.fetch("dashboard", {}, schema)).toEqual({ revision: 1 });
  });

  it("keeps the current publication when a previously requested file is absent from the new generation", async () => {
    const first = generation(firstId, 1);
    const second = generation(secondId, 2);
    let current = first;
    vi.stubGlobal("crypto", nodeWebCrypto());
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("current.json")) return new Response(current.pointer);
      if (url.includes(first.id) && url.endsWith("manifest.json")) return new Response(first.manifest);
      if (url.includes(second.id) && url.endsWith("manifest.json")) return new Response(second.manifest);
      if (url.includes(first.id)) return new Response(first.data);
      return new Response("not found", { status: 404 });
    }));
    const client = new DatasetClient("/site", true);
    await client.fetch("risk", {}, schema);
    current = second;
    await expect(client.hasPublicationUpdate()).rejects.toThrow("HTTP 404");
    expect(await client.fetch("dashboard", {}, schema)).toEqual({ revision: 1 });
  });

  it("fails rather than mixing generations if a pinned file disappears", async () => {
    const first = generation(firstId, 1);
    vi.stubGlobal("crypto", nodeWebCrypto());
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("current.json")) return new Response(first.pointer);
      if (url.endsWith("manifest.json")) return new Response(first.manifest);
      return new Response("not found", { status: 404 });
    }));
    await expect(new DatasetClient("/site", true).fetch("risk", {}, schema)).rejects.toThrow("HTTP 404");
  });

  it("cancels an individual caller while the shared manifest is loading", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    const controller = new AbortController();
    const request = new DatasetClient("/site", true).fetch("risk", {}, schema, controller.signal);
    controller.abort();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    vi.clearAllTimers();
  });

  it("bounds response body reading, not just response headers", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => ({
      ok: true,
      arrayBuffer: () => new Promise((_resolve, reject) => {
        options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      }),
    })));
    const request = new DatasetClient("/site").fetch("risk", {}, schema);
    const assertion = expect(request).rejects.toThrow("timed out");
    await vi.advanceTimersByTimeAsync(10_000);
    await assertion;
  });

  it("notifies once when a newer verified publication is available", async () => {
    vi.useFakeTimers();
    const check = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
    const reload = vi.fn();
    const stop = watchPublication({ hasPublicationUpdate: check }, reload);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(check).toHaveBeenCalledTimes(2);
    expect(reload).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(reload).toHaveBeenCalledTimes(1);
    stop();
  });
});
