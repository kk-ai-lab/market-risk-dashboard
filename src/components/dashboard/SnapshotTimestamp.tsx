import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { formatDateTime, formatNumber } from "@/lib/format";
import { snapshotAge } from "@/lib/snapshotAge";

export function SnapshotTimestamp({ generatedAt }: { generatedAt: string | null | undefined }) {
  const { t, i18n } = useTranslation("dashboard");
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    const update = () => setNow(Date.now());
    update();
    const interval = window.setInterval(update, 60_000);
    window.addEventListener("focus", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, [generatedAt]);

  const age = snapshotAge({ generatedAt, now });

  return (
    <div className="border-y border-hairline py-2 text-xs text-muted-foreground" data-testid="snapshot-timestamp">
      <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono">
        <span>
          {t("statusBar.generated")}: {age.kind === "known" ? (
            <time dateTime={age.generatedAt}>{formatDateTime(age.generatedAt, i18n.language)}</time>
          ) : t("snapshot.unknown")}
        </span>
        <span data-testid="snapshot-age">
          {t("snapshot.age")}: {age.kind === "known"
            ? t("snapshot.minutesOld", { minutes: formatNumber(age.ageMinutes, i18n.language, 0) })
            : t("snapshot.unknown")}
        </span>
      </div>
      <p className="mt-1 leading-relaxed">{t("snapshot.meaning")}</p>
    </div>
  );
}
