import { HashRouter } from "react-router-dom";
import { useEffect } from "react";
import { AppRoutes } from "./router";
import { ErrorBoundary } from "@/components/layout/ErrorBoundary";
import { datasetClient } from "@/lib/api";
import { watchPublication } from "@/lib/publication";

/**
 * App shell.
 * Uses Hash Router (frozen, architecture §1.2) for GitHub Pages refresh stability (#/zh/overview).
 * See src/router.tsx for the route table and placeholder pages; layout (Navbar/Footer/LanguageSwitch) is implemented in T04.
 */
export default function App() {
  useEffect(() => {
    if (import.meta.env.VITE_VERSIONED_DATA !== "true") return;
    return watchPublication(datasetClient, () => window.location.reload());
  }, []);
  return (
    <HashRouter>
      <ErrorBoundary>
        <AppRoutes />
      </ErrorBoundary>
    </HashRouter>
  );
}
