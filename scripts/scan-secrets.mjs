#!/usr/bin/env node
import { lstatSync, readFileSync, readdirSync } from "node:fs";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { brotliDecompressSync, gunzipSync } from "node:zlib";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = resolve(__dirname, "..");
const MAX_FILE_BYTES = 2 * 1024 * 1024;

const PATTERNS = [
  /["']?(api[_-]?key|apikey|token|secret|password)["']?\s*[:=]\s*["']?[^&\s"'\u4e00-\u9fff]{8,}/i,
];

const DIST_PATTERNS = [
  /["']?(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token)["']?\s*[:=]\s*["']?[A-Za-z0-9._~+/=-]{16,}/i,
  /["']?(?:secret|password)["']?\s*[:=]\s*["'][^"']{8,}["']/i,
];

const SOURCE_PATTERNS = [
  /["']?(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token)["']?\s*[:=]\s*["']?[0-9a-fA-F]{24,}\b/,
  /\bCG-[A-Za-z0-9]{16,}\b/,
];

const LITERAL_ONLY_PREFIXES = ["tests/", "test_"];
const SCAN_TIERS = {
  source: SOURCE_PATTERNS,
  publicData: PATTERNS,
  builtOutput: DIST_PATTERNS,
  runLog: PATTERNS,
  literalOnly: [],
};

let filesScanned = 0;
let filesSkipped = 0;
const hits = [];

function parseArguments(argv) {
  const values = new Map();
  for (let i = 0; i < argv.length; i += 1) {
    const name = argv[i];
    if (!["--root", "--artifact-root", "--run-logs-root"].includes(name)) {
      throw new Error("unknown scan option");
    }
    if (values.has(name) || !argv[i + 1] || argv[i + 1].startsWith("--")) {
      throw new Error("invalid scan option value");
    }
    values.set(name, argv[i + 1]);
    i += 1;
  }

  const root = resolve(values.get("--root") ?? DEFAULT_ROOT);
  return {
    root,
    artifactRoot: values.has("--artifact-root")
      ? resolve(root, values.get("--artifact-root"))
      : null,
    runLogsRoot: values.has("--run-logs-root")
      ? resolve(root, values.get("--run-logs-root"))
      : null,
  };
}

function readTextFile(file) {
  const stat = lstatSync(file);
  if (!stat.isFile() || stat.size > MAX_FILE_BYTES) {
    throw new Error("unsupported file size or type");
  }

  let bytes = readFileSync(file);
  if (bytes.length > MAX_FILE_BYTES) throw new Error("file exceeds scan limit");

  try {
    const extension = extname(file).toLowerCase();
    if (extension === ".gz") {
      bytes = gunzipSync(bytes, { maxOutputLength: MAX_FILE_BYTES });
    } else if (extension === ".br") {
      bytes = brotliDecompressSync(bytes, { maxOutputLength: MAX_FILE_BYTES });
    }
  } catch {
    throw new Error("compressed file is invalid or exceeds scan limit");
  }

  if (bytes.length > MAX_FILE_BYTES || bytes.includes(0)) {
    throw new Error("file is binary or exceeds scan limit");
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

function parseDotenv(text) {
  const script = [
    "import json, sys",
    "from io import StringIO",
    "from dotenv import dotenv_values",
    "print(json.dumps(dotenv_values(stream=StringIO(sys.stdin.read()), interpolate=True)))",
  ].join("\n");
  const localPython = process.platform === "win32"
    ? join(__dirname, "..", ".venv", "Scripts", "python.exe")
    : join(__dirname, "..", ".venv", "bin", "python");
  const executables = [process.env.PYTHON_EXECUTABLE, localPython, "python", "python3"]
    .filter(Boolean)
    .filter((executable, index, all) => all.indexOf(executable) === index);

  for (const executable of executables) {
    try {
      const output = execFileSync(executable, ["-c", script], {
        encoding: "utf-8",
        input: text,
        maxBuffer: MAX_FILE_BYTES * 8,
      });
      const values = JSON.parse(output);
      if (!values || typeof values !== "object" || Array.isArray(values)) {
        throw new Error("invalid dotenv values");
      }
      return values;
    } catch (error) {
      if (error.code === "ENOENT") continue;
      throw new Error("dotenv values cannot be resolved");
    }
  }
  throw new Error("dotenv values cannot be resolved");
}

function isSecretSetting(name) {
  return /(API[_-]?KEY|TOKEN|SECRET)/i.test(name);
}

function usableLiteral(value) {
  return Boolean(value) && value.length >= 8;
}

function literalVariants(secret) {
  const encoded = encodeURIComponent(secret);
  const encodedPattern = encoded
    .split(/(%[0-9A-F]{2})/g)
    .map((part) => {
      if (!/^%[0-9A-F]{2}$/.test(part)) {
        return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      }
      const hexPattern = (digit) =>
        digit.toLowerCase() === digit.toUpperCase()
          ? digit
          : `[${digit.toLowerCase()}${digit.toUpperCase()}]`;
      return `%${hexPattern(part[1])}${hexPattern(part[2])}`;
    })
    .join("");
  return [
    { value: secret, encoding: "raw", pattern: new RegExp(secret.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) },
    { value: encoded, encoding: "url", pattern: new RegExp(encodedPattern) },
  ].map((literal) => ({
    ...literal,
    redactionPattern: new RegExp(literal.pattern.source, "g"),
  }));
}

function envLiterals(root) {
  const values = [];
  const envFile = join(root, ".env");
  try {
    const dotenv = parseDotenv(readTextFile(envFile));
    filesScanned += 1;
    for (const [name, value] of Object.entries(dotenv)) {
      if (isSecretSetting(name) && usableLiteral(value)) values.push(value);
    }
  } catch (error) {
    if (error.code !== "ENOENT") filesSkipped += 1;
  }

  for (const [name, value] of Object.entries(process.env)) {
    if (isSecretSetting(name) && usableLiteral(value)) values.push(value);
  }
  const variants = values.flatMap(literalVariants);
  return [
    ...new Map(
      variants.map((literal) => [`${literal.encoding}\u0000${literal.value}`, literal]),
    ).values(),
  ];
}

function normalizedRelative(root, file) {
  return relative(root, file).split(sep).join("/");
}

function collectTree(root, { required = false, nonEmpty = false } = {}) {
  let stat;
  try {
    stat = lstatSync(root);
  } catch (error) {
    if (error.code === "ENOENT" && !required) return [];
    filesSkipped += 1;
    return [];
  }
  if (!stat.isDirectory()) {
    filesSkipped += 1;
    return [];
  }

  const files = [];
  const visit = (directory) => {
    let entries;
    try {
      entries = readdirSync(directory, { withFileTypes: true });
    } catch {
      filesSkipped += 1;
      return;
    }
    for (const entry of entries) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile()) files.push(path);
      else filesSkipped += 1;
    }
  };
  visit(root);
  if (required && nonEmpty && files.length === 0) filesSkipped += 1;
  return files;
}

function trackedFiles(root) {
  try {
    const out = execFileSync("git", ["ls-files", "-z"], {
      cwd: root,
      encoding: "utf-8",
      maxBuffer: 64 * 1024 * 1024,
    });
    return out.split("\u0000").filter(Boolean).map((path) => resolve(root, path));
  } catch {
    throw new Error("cannot enumerate tracked files");
  }
}

function addFile(files, file, tier, label) {
  files.set(resolve(file), { file: resolve(file), tier, label });
}

function addTree(files, treeRoot, tier, labelRoot, options) {
  for (const file of collectTree(treeRoot, options)) {
    const suffix = normalizedRelative(treeRoot, file);
    addFile(files, file, tier, `${labelRoot}/${suffix}`);
  }
}

function repositoryFiles(root, runLogsRoot) {
  const files = new Map();
  for (const file of trackedFiles(root)) {
    const label = normalizedRelative(root, file);
    const literalOnly = LITERAL_ONLY_PREFIXES.some(
      (prefix) => label === prefix || label.startsWith(prefix),
    );
    const tier = label.startsWith("public/data/")
      ? "publicData"
      : literalOnly
        ? "literalOnly"
        : "source";
    addFile(files, file, tier, label);
  }

  addTree(files, join(root, "public/data"), "publicData", "public/data", {
    required: true,
    nonEmpty: true,
  });
  addTree(files, join(root, "dist"), "builtOutput", "dist", {});
  addTree(files, join(root, "artifacts/logs"), "runLog", "artifacts/logs", {});
  if (runLogsRoot) {
    addTree(files, runLogsRoot, "runLog", "run-logs", {
      required: true,
      nonEmpty: true,
    });
  }
  return [...files.values()];
}

function artifactFiles(artifactRoot, repositoryRoot, runLogsRoot) {
  const files = new Map();
  const siteFiles = collectTree(artifactRoot, { required: true, nonEmpty: true });
  const dataFiles = siteFiles.filter((file) =>
    normalizedRelative(artifactRoot, file).startsWith("data/"),
  );
  if (dataFiles.length === 0) filesSkipped += 1;
  const dataPaths = new Set(dataFiles.map((file) => resolve(file)));

  for (const file of siteFiles) {
    const tier = dataPaths.has(resolve(file)) ? "publicData" : "builtOutput";
    addFile(files, file, tier, normalizedRelative(artifactRoot, file));
  }
  addTree(files, join(repositoryRoot, "artifacts/logs"), "runLog", "artifacts/logs", {});
  if (runLogsRoot) {
    addTree(files, runLogsRoot, "runLog", "run-logs", {
      required: true,
      nonEmpty: true,
    });
  }
  return [...files.values()];
}

function scanFile(item, literals) {
  let text;
  try {
    text = readTextFile(item.file);
  } catch {
    filesSkipped += 1;
    return;
  }
  filesScanned += 1;

  const label = [...literals]
    .sort((left, right) => right.value.length - left.value.length)
    .reduce(
      (safeLabel, literal) =>
        safeLabel.replace(literal.redactionPattern, "[REDACTED]"),
      item.label,
    );
  const patterns = SCAN_TIERS[item.tier];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i += 1) {
    if (patterns.some((pattern) => pattern.test(lines[i]))) {
      hits.push(`${label}:${i + 1}: pattern match`);
    }
  }
  if (literals.some((literal) => literal.pattern.test(text))) {
    hits.push(`${label}: literal env value (masked)`);
  }
}

function main() {
  let options;
  try {
    options = parseArguments(process.argv.slice(2));
  } catch {
    console.error("[scan-secrets] FATAL: invalid scan arguments");
    return 1;
  }

  let files;
  try {
    files = options.artifactRoot
      ? artifactFiles(options.artifactRoot, options.root, options.runLogsRoot)
      : repositoryFiles(options.root, options.runLogsRoot);
  } catch {
    console.error("[scan-secrets] FATAL: cannot inventory scan inputs");
    return 1;
  }

  const literals = envLiterals(options.root);
  for (const item of files) scanFile(item, literals);

  console.log(`[scan-secrets] scanned ${filesScanned} files (${filesSkipped} skipped), ${hits.length} hit(s)`);
  for (const hit of hits) console.log(`  [SECRET] ${hit}`);
  if (hits.length > 0) {
    console.log("[scan-secrets] result: FAILED — fix before publish");
    return 1;
  }
  if (filesSkipped > 0) {
    console.log("[scan-secrets] result: FAILED — scan coverage incomplete; inspect skipped files");
    return 1;
  }
  console.log("[scan-secrets] result: PASSED");
  return 0;
}

process.exitCode = main();
