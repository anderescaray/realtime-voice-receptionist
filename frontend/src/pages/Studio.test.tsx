import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { Studio } from "./Studio";
import { STUDIO_PATIENTS } from "../studio/patients";
import { NEW_PATIENT, getStudioCards } from "../studio/cards";
import { formatDob } from "../studio/formatDob";

beforeEach(() => {
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn() },
  });
});

const timePattern = /\b\d{1,2}:\d{2}\b/;
const weekdayPattern =
  /\b(lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/i;

describe("Studio", () => {
  it.each(["es", "en"] as const)("renders six role cards in %s", (lang) => {
    render(<Studio lang={lang} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
  });

  it.each(["es", "en"] as const)("shows patient names and formatted dob (%s)", (lang) => {
    render(<Studio lang={lang} />);
    for (const id of Object.keys(STUDIO_PATIENTS) as (keyof typeof STUDIO_PATIENTS)[]) {
      const p = STUDIO_PATIENTS[id];
      expect(screen.getByText(new RegExp(p.firstName))).toBeInTheDocument();
      expect(screen.getByText(new RegExp(formatDob(p.dob, lang).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeInTheDocument();
    }
  });

  it("does not put new patient in STUDIO_PATIENTS", () => {
    const seededFirstNames = Object.values(STUDIO_PATIENTS).map((p) => p.firstName);
    expect(seededFirstNames).not.toContain(NEW_PATIENT.firstName);
  });

  it.each(["es", "en"] as const)("card copy has no concrete appointment times (%s)", (lang) => {
    const cards = getStudioCards(lang);
    for (const card of cards) {
      const blob = [card.title, card.who, card.goal, ...card.hints].join(" ");
      expect(blob).not.toMatch(timePattern);
      expect(blob).not.toMatch(weekdayPattern);
    }
  });

  it("does not call getUserMedia when selecting a card", async () => {
    const getUserMedia = vi.fn();
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    const user = userEvent.setup();
    render(<Studio lang="en" />);
    await user.click(screen.getByTestId("studio-card-new_patient"));
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(screen.getByTestId("active-role")).toBeInTheDocument();
  });
});