import { useEffect, useState } from "react";

type DocumentTheme = "dark" | "light";

function readDocumentTheme(): DocumentTheme {
  return document.documentElement.classList.contains("light") ? "light" : "dark";
}

export function useDocumentTheme(): DocumentTheme {
  const [theme, setTheme] = useState<DocumentTheme>(readDocumentTheme);

  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(readDocumentTheme()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return theme;
}
