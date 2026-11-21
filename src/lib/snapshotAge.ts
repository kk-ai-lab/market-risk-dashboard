import { utcDateTime } from "@/schemas";

export type SnapshotAge =
  | { kind: "known"; generatedAt: string; ageMinutes: number }
  | { kind: "unknown" };

export function snapshotAge({ generatedAt, now }: { generatedAt: unknown; now: number }): SnapshotAge {
  const parsed = utcDateTime.safeParse(generatedAt);
  if (!parsed.success || !Number.isFinite(now)) return { kind: "unknown" };
  const generatedMs = Date.parse(parsed.data);
  if (!Number.isFinite(generatedMs) || generatedMs > now) return { kind: "unknown" };
  return { kind: "known", generatedAt: parsed.data, ageMinutes: Math.floor((now - generatedMs) / 60_000) };
}
