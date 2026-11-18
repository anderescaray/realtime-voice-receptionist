import { useCallback, useEffect, useState } from "react";
import {
  langFromSearch,
  resolveInitialLang,
  writeStoredLang,
  type Lang,
} from "../i18n";

export function useLang(): [Lang, (lang: Lang) => void] {
  const [lang, setLangState] = useState<Lang>(resolveInitialLang);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    const onPop = () => {
      const fromUrl = langFromSearch(window.location.search);
      if (fromUrl) setLangState(fromUrl);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    writeStoredLang(next);
    const url = new URL(window.location.href);
    url.searchParams.set("lang", next);
    window.history.replaceState(null, "", url.toString());
    document.documentElement.lang = next;
  }, []);

  return [lang, setLang];
}