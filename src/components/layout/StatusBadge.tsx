import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { badgeFor, currentFreshness, currentSourceFreshness, elapsedFreshness, macroObservationFreshness } from "@/lib/freshness";
import { freshTone, type FreshTone } from "@/lib/riskColors";
import { Badge } from "@/components/ui/Badge";
import type { FreshnessStatus, MacroFreshnessPolicy } from "@/schemas";

/**
 * StatusBadge: freshness six-state badge (architecture §8.5, ADR-0002).
 * Five states → icon + text + muted treatment; "fresh" uses no saturated color
 * (expected state), only stale/missing earn a warm tone. Color is never the only expression.
 * #66: a cache-replayed dataset renders a distinct badge (fromCache) instead of sharing
 * the delayed/degraded look.
 */
export interface StatusBadgeProps {
  status: FreshnessStatus;
  /** Whether to show the descriptive text (label only by default). */
  withDescription?: boolean;
  /** Cache replay (#66): served from the last-good cache, not fetched live. */
  fromCache?: boolean;
  dataset?: string;
  generatedAt?: string;
  sourceUpdatedAt?: string | null;
  sourceExpectedMinutes?: number;
  observationAt?: string | null;
  observationPolicy?: MacroFreshnessPolicy | null;
  className?: string;
}

const TONE_TO_VARIANT: Record<FreshTone, "freshOk" | "freshWarn" | "freshBad"> = {
  ok: "freshOk",
  warn: "freshWarn",
  bad: "freshBad",
  na: "freshOk",
};

const TONE_TO_DOT: Record<FreshTone, string> = {
  ok: "bg-fresh-ok",
  warn: "bg-fresh-warn",
  bad: "bg-fresh-bad",
  na: "bg-muted-foreground",
};

export function StatusBadge({ status: publishedStatus, dataset, generatedAt, sourceUpdatedAt, sourceExpectedMinutes, observationAt, observationPolicy, withDescription = false, fromCache = false, className }: StatusBadgeProps) {
  const { t } = useTranslation("common");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!(dataset && generatedAt) && !(observationAt && observationPolicy)) return;
    const update = () => setNow(Date.now());
    const timer = window.setInterval(update, 60_000);
    window.addEventListener("focus", update);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", update); };
  }, [dataset, generatedAt, observationAt, observationPolicy]);
  const snapshotStatus = dataset && generatedAt ? currentFreshness(publishedStatus, dataset, generatedAt, now, sourceExpectedMinutes) : publishedStatus;
  const sourceAge = dataset && sourceExpectedMinutes ? elapsedFreshness(dataset, sourceUpdatedAt ?? "", now, sourceExpectedMinutes) : "fresh";
  const sourceStatus = currentSourceFreshness(snapshotStatus, sourceAge);
  const status = observationAt && observationPolicy
    ? macroObservationFreshness(sourceStatus, observationAt, observationPolicy, now)
    : sourceStatus;
  const badge = badgeFor(status, fromCache);
  const tone = freshTone(status);
  const testId = fromCache ? "status-badge-cache" : `status-badge-${status}`;
  const age = dataset && generatedAt ? elapsedFreshness(dataset, generatedAt, now, sourceExpectedMinutes) : "fresh";
  const showAge = age !== "fresh" && (fromCache || !["fresh", "delayed", "stale"].includes(publishedStatus));

  return (
    <span className={`inline-flex items-center gap-2 ${className ?? ""}`} title={t(badge.descriptionKey)}>
      <Badge variant={TONE_TO_VARIANT[tone]} data-testid={testId}>
        <span className={`h-1.5 w-1.5 rounded-full ${TONE_TO_DOT[tone]}`} aria-hidden />
        {t(badge.labelKey)}
      </Badge>
      {showAge ? <Badge variant={TONE_TO_VARIANT[freshTone(age)]} data-testid={`status-badge-age-${age}`} title={t("status.snapshotAgeDesc")}>{t(`status.snapshotAge.${age}`)}</Badge> : null}
      {sourceAge !== "fresh" && (fromCache || !["fresh", "delayed", "stale"].includes(snapshotStatus)) ? <Badge variant={TONE_TO_VARIANT[freshTone(sourceAge)]} data-testid={`status-badge-source-${sourceAge}`} title={t("status.sourceAgeDesc")}>{t(`status.sourceAge.${sourceAge}`)}</Badge> : null}
      {withDescription ? <span className="text-xs text-muted-foreground">{t(badge.descriptionKey)}</span> : null}
    </span>
  );
}
