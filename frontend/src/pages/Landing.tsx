import { CallPanel } from "../components/CallPanel";
import type { Lang } from "../i18n";
import { t } from "../i18n";

type LandingProps = { lang: Lang };

export function Landing({ lang }: LandingProps) {
  return (
    <main className="page">
      <p className="eyebrow">{t(lang, "clinicName")}</p>
      <h1>{t(lang, "landingTitle")}</h1>
      <p className="lead">{t(lang, "landingLead")}</p>
      <ul className="pitch-list">
        <li>{t(lang, "landingPoint1")}</li>
        <li>{t(lang, "landingPoint2")}</li>
        <li>{t(lang, "landingPoint3")}</li>
      </ul>
      <p>
        <a className="studio-promo" href="#/studio">
          {t(lang, "landingStudioLink")}
        </a>
      </p>
      <CallPanel lang={lang} studioLink />
    </main>
  );
}