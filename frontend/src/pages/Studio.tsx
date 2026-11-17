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

      <div className="card-grid" role="list">
        {cards.map((card) => (
          <button
            key={card.id}
            type="button"
            role="listitem"
            className={`role-card${selectedId === card.id ? " role-card--active" : ""}`}
            onClick={() => setSelectedId(card.id)}
            data-testid={`studio-card-${card.id}`}
            aria-pressed={selectedId === card.id}
          >
            <h2>{card.title}</h2>
            <p>
              <strong>{card.whoLabel}:</strong> {card.who}
            </p>
            <p>
              <strong>{card.goalLabel}:</strong> {card.goal}
            </p>
          </button>
        ))}
      </div>

      {activeCard ? <CallPanel lang={lang} activeCard={activeCard} /> : null}
    </main>
  );
}