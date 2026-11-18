import { useState } from "react";
import { CallPanel } from "../components/CallPanel";
import { getStudioCards, type StudioCardId, type StudioCardView } from "../studio/cards";
import type { Lang } from "../i18n";
import { t } from "../i18n";

type StudioProps = { lang: Lang };

export function Studio({ lang }: StudioProps) {
  const cards = getStudioCards(lang);
  const [selectedId, setSelectedId] = useState<StudioCardId | null>(null);
  const activeCard: StudioCardView | null =
    selectedId ? cards.find((c) => c.id === selectedId) ?? null : null;

  return (
    <main className="page">
      <h1>{t(lang, "studioTitle")}</h1>
      <p className="lead">{t(lang, "studioLead")}</p>
      <p>{t(lang, "studioPickRole")}</p>

      <ul className="card-grid">
        {cards.map((card) => (
          <li key={card.id}>
            <button
              type="button"
              className={`role-card${selectedId === card.id ? " role-card--active" : ""}`}
              onClick={() => setSelectedId(card.id)}
              data-testid={`studio-card-${card.id}`}
              aria-pressed={selectedId === card.id}
            >
              <span className="role-card__title">{card.title}</span>
              <span className="role-card__who">
                <strong>{card.whoLabel}:</strong> {card.who}
              </span>
              <span className="role-card__goal">
                <strong>{card.goalLabel}:</strong> {card.goal}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {activeCard ? <CallPanel lang={lang} activeCard={activeCard} /> : null}
    </main>
  );
}