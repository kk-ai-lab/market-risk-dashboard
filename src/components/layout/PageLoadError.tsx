import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";

export function PageLoadError() {
  const { t } = useTranslation("common");
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 p-8 text-center" role="alert">
      <h1 className="text-lg font-semibold text-foreground">{t("error.pageTitle")}</h1>
      <p className="max-w-md text-sm text-muted-foreground">{t("error.pageMessage")}</p>
      <Button variant="outline" onClick={() => window.location.reload()}>{t("error.reload")}</Button>
    </div>
  );
}
