import { z } from "zod";

const sha256 = z.string().regex(/^[0-9a-f]{64}$/);
const pointerSchema = z.object({ version: z.literal(1), generation_id: sha256, manifest_sha256: sha256 }).strict();
const manifestSchema = z.object({
  version: z.literal(1), generation_id: sha256, parent_id: sha256.nullable(),
  producer_revision: z.string().min(1), created_at: z.string().datetime({ offset: true }),
  files: z.record(z.string(), sha256),
}).strict();

type Manifest = z.infer<typeof manifestSchema>;
type Fetcher = (url: string, signal?: AbortSignal) => Promise<ArrayBuffer>;

function safePath(path: string): boolean {
  const parts = path.split("/");
  return parts.length >= 2 && ["latest", "history", "metadata"].includes(parts[0])
    && parts.every((part) => part !== "." && part !== ".." && /^[A-Za-z0-9._-]+$/.test(part))
    && path.endsWith(".json");
}

async function digest(bytes: ArrayBuffer): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function waitForManifest(promise: Promise<Manifest>, signal?: AbortSignal): Promise<Manifest> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", abort, { once: true });
    void promise.then(
      (value) => { signal.removeEventListener("abort", abort); resolve(value); },
      (error: unknown) => { signal.removeEventListener("abort", abort); reject(error); },
    );
  });
}

/** Pins a single validated publication for the lifetime of the client. */
export class PublicationSession {
  private pinned: Promise<Manifest> | undefined;
  private readonly dataRoot: string;
  private readonly requested = new Set<string>();

  constructor(baseUrl: string, private readonly fetcher: Fetcher) {
    this.dataRoot = `${baseUrl.replace(/\/+$/, "")}/data`;
  }

  private manifest(): Promise<Manifest> {
    if (!this.pinned) {
      const pending = this.load();
      this.pinned = pending;
      void pending.catch(() => { if (this.pinned === pending) this.pinned = undefined; });
    }
    return this.pinned;
  }

  private async load(): Promise<Manifest> {
    const pointerBytes = await this.fetcher(`${this.dataRoot}/current.json`);
    const pointer = pointerSchema.parse(JSON.parse(new TextDecoder().decode(pointerBytes)) as unknown);
    const url = `${this.dataRoot}/generations/${pointer.generation_id}/manifest.json`;
    const bytes = await this.fetcher(url);
    if (await digest(bytes) !== pointer.manifest_sha256) throw new Error("Publication manifest hash mismatch");
    const manifest = manifestSchema.parse(JSON.parse(new TextDecoder().decode(bytes)) as unknown);
    if (manifest.generation_id !== pointer.generation_id || !Object.keys(manifest.files).length
      || Object.keys(manifest.files).some((path) => !safePath(path))) throw new Error("Invalid publication manifest");
    return manifest;
  }

  async fetch(path: string, signal?: AbortSignal): Promise<unknown> {
    if (!safePath(path)) throw new Error("Invalid publication data path");
    const manifest = await waitForManifest(this.manifest(), signal);
    this.requested.add(path);
    return this.read(manifest, path, signal);
  }

  private async read(manifest: Manifest, path: string, signal?: AbortSignal): Promise<unknown> {
    const expected = manifest.files[path];
    if (!expected) throw new Error("Data file is not in the publication manifest");
    const url = `${this.dataRoot}/generations/${manifest.generation_id}/${path}`;
    const bytes = await this.fetcher(url, signal);
    if (await digest(bytes) !== expected) throw new Error("Publication data hash mismatch");
    return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
  }

  async hasUpdate(): Promise<boolean> {
    if (!this.pinned) return false;
    const current = await this.pinned;
    const candidate = await this.load();
    if (candidate.generation_id === current.generation_id) return false;
    await Promise.all([...this.requested].map((path) => this.read(candidate, path)));
    return true;
  }
}

/** Checks for a newer generation; failed checks leave the verified generation in place. */
export function watchPublication(client: { hasPublicationUpdate(): Promise<boolean> }, onUpdate: () => void): () => void {
  let active = true;
  let checking = false;
  let notified = false;
  const check = async () => {
    if (!active || checking || notified || document.visibilityState === "hidden") return;
    checking = true;
    try {
      if (await client.hasPublicationUpdate() && active) {
        notified = true;
        onUpdate();
      }
    } catch {
      return;
    } finally {
      checking = false;
    }
  };
  const timer = window.setInterval(() => { void check(); }, 60_000);
  window.addEventListener("focus", check);
  return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", check); };
}
