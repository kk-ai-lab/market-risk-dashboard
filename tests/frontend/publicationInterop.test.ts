import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { afterEach, expect, it, vi } from "vitest";
import { DatasetClient } from "@/lib/api";
import { RiskEnvelope, FreshnessDocument } from "@/schemas";
import { RiskTrendSlice } from "@/schemas/history";
import { nodeWebCrypto } from "./helpers/nodeWebCrypto";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("reads a fully validated synthetic snapshot produced by the Python staging entry point", async () => {
  const python = existsSync(".venv/bin/python") ? ".venv/bin/python" : "python3";
  const script = `
import json, tempfile
from pathlib import Path
import scripts.stage_publication as staging
from pipeline.collectors.macro import SERIES_GROUPS
from pipeline.validation.ci_checks import run_all
from tests.pipeline.factories import DEFAULT_NOW, REMOVE, build_data_dir
from tests.pipeline.test_data_only import LEGACY_FILES, _publish_production_metadata
root = Path(tempfile.mkdtemp(prefix="mrd-publication-interop-"))
support = {f"history/macro/{group}.{slice}.json": {} for group in SERIES_GROUPS for slice in ("30d", "90d")}
source = build_data_dir(root / "candidate", latest={name: REMOVE for name in LEGACY_FILES}, support=support)
_publish_production_metadata(source)
staging.run_all = lambda path: run_all(path, now=DEFAULT_NOW)
pointer = staging.stage_candidate(source, root / "store", parent_id=None, revision="fixture", created_at="2026-11-14T12:00:00Z")
generation = root / "store" / "generations" / pointer["generation_id"]
files = ["latest/risk.json", "history/risk/30d.json", "metadata/freshness.json"]
print(json.dumps({"pointer": (root / "store" / "current.json").read_text(), "manifest": (generation / "manifest.json").read_text(), "data": {name: (generation / name).read_text() for name in files}}))
`;
  const output = JSON.parse(execFileSync(python, ["-c", script], { encoding: "utf8", timeout: 30_000 })) as { pointer: string; manifest: string; data: Record<string, string> };
  vi.stubGlobal("crypto", nodeWebCrypto());
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/current.json")) return new Response(output.pointer);
    if (url.endsWith("/manifest.json")) return new Response(output.manifest);
    const relative = url.split("/generations/")[1]?.split("/").slice(1).join("/");
    const body = relative ? output.data[relative] : undefined;
    return body === undefined ? new Response("missing", { status: 404 }) : new Response(body);
  }));
  const client = new DatasetClient("/market-risk-dashboard/", true);
  const risk = await client.fetch<RiskEnvelope>("risk", {}, RiskEnvelope);
  const history = await client.fetch<RiskTrendSlice>("risk", { slice: "30d" }, RiskTrendSlice);
  const freshness = await client.fetch<FreshnessDocument>("freshness", {}, FreshnessDocument);
  expect(risk.payload.total_score).toBe(62.5);
  expect(history.at(-1)?.total_score).toBe(62.5);
  expect(freshness.datasets.risk.status).toBe("fresh");
}, 45_000);
