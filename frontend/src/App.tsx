import { useHashRoute } from "./hooks/useHashRoute";
import { useLang } from "./hooks/useLang";
import { t, type Lang } from "./i18n";
import { Landing } from "./pages/Landing";
import { Studio } from "./pages/Studio";

function LangSwitch({ lang, onChange }: { lang: Lang; onChange: (l: Lang) => void }) {
  return (
    <div className="lang-switch" role="group" aria-label={t(lang, "langSwitchLabel")}>
      <button
        type="button"
        className={lang === "es" ? "lang-switch__btn lang-switch__btn--on" : "lang-switch__btn"}
        onClick={() => onChange("es")}
      >
        ES
      </button>
      <span aria-hidden="true">|</span>
      <button
        type="button"
        className={lang === "en" ? "lang-switch__btn lang-switch__btn--on" : "lang-switch__btn"}
        onClick={() => onChange("en")}
      >
        EN
      </button>
    </div>
  );
}

export function App() {
  const [lang, setLang] = useLang();
  const route = useHashRoute();

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="site-header__brand">
          <span className="site-header__name">{t(lang, "productName")}</span>
          <span className="site-header__clinic">{t(lang, "clinicName")}</span>
        </div>
        <nav className="site-nav" aria-label={t(lang, "navLanding")}>
          <a href="#/" className={route === "landing" ? "nav-link nav-link--active" : "nav-link"}>
            {t(lang, "navLanding")}
          </a>
          <a
            href="#/studio"
            className={route === "studio" ? "nav-link nav-link--active" : "nav-link"}
          >
            {t(lang, "navStudio")}
          </a>
        </nav>
        <LangSwitch lang={lang} onChange={setLang} />
      </header>

      {route === "studio" ? <Studio lang={lang} /> : <Landing lang={lang} />}
    </div>
  );
}