import type { Lang } from "../i18n";
import { formatDob, fullName } from "./formatDob";
import { STUDIO_PATIENTS } from "./patients";

export const NEW_PATIENT = {
  firstName: "Lucía",
  lastName1: "Ferrer",
  lastName2: "Soler",
  dob: "1990-04-15",
  phone: "+44 20 7946 0987",
} as const;

export type StudioCardId =
  | "new_patient"
  | "earliest_checkup"
  | "move"
  | "cancel"
  | "toothache"
  | "price_question";

export type StudioCardView = {
  id: StudioCardId;
  title: string;
  whoLabel: string;
  who: string;
  goalLabel: string;
  goal: string;
  hintsLabel: string;
  hints: string[];
};

const CONTENT: Record<Lang, Record<StudioCardId, Omit<StudioCardView, "id">>> = {
  es: {
    new_patient: {
      title: "Paciente nueva",
      whoLabel: "Quién eres",
      who: `${fullName(NEW_PATIENT)}, nacida el ${formatDob(NEW_PATIENT.dob, "es")}. Teléfono: ${NEW_PATIENT.phone}.`,
      goalLabel: "Objetivo",
      goal: "Darte de alta y reservar una primera revisión.",
      hintsLabel: "Puede salir",
      hints: [
        "Tu nombre completo y fecha de nacimiento",
        "Motivo: primera visita / revisión",
        "Preferencia de días si te la preguntan",
      ],
    },
    earliest_checkup: {
      title: "La cita más pronto",
      whoLabel: "Quién eres",
      who: `Paciente conocido (${fullName(STUDIO_PATIENTS.p0097)}), nacido el ${formatDob(STUDIO_PATIENTS.p0097.dob, "es")}.`,
      goalLabel: "Objetivo",
      goal: "La revisión más temprana que haya con cualquier dentista.",
      hintsLabel: "Puede salir",
      hints: [
        "Confirmar identidad con nombre y fecha de nacimiento",
        "Flexibilidad de horario si ofrecen varias opciones",
      ],
    },
    move: {
      title: "Cambiar cita",
      whoLabel: "Quién eres",
      who: `Paciente conocida (${fullName(STUDIO_PATIENTS.p0181)}), nacida el ${formatDob(STUDIO_PATIENTS.p0181.dob, "es")}.`,
      goalLabel: "Objetivo",
      goal: "Mover tu próxima cita a una semana más tarde, por la tarde. No recuerdas la fecha: pide que te la digan.",
      hintsLabel: "Puede salir",
      hints: [
        "Pedir la fecha y hora actuales de la cita",
        "Indicar que prefieres tarde, una semana después",
      ],
    },
    cancel: {
      title: "Anular cita",
      whoLabel: "Quién eres",
      who: `Paciente conocida (${fullName(STUDIO_PATIENTS.p0042)}), nacida el ${formatDob(STUDIO_PATIENTS.p0042.dob, "es")}.`,
      goalLabel: "Objetivo",
      goal: "Anular tu próxima cita. No recuerdas la fecha: pide que te la confirmen.",
      hintsLabel: "Puede salir",
      hints: [
        "Confirmar qué cita se va a cancelar",
        "Agradecer la confirmación",
      ],
    },
    toothache: {
      title: "Urgencia por dolor",
      whoLabel: "Quién eres",
      who: `Paciente conocido (${fullName(STUDIO_PATIENTS.p0157)}), nacido el ${formatDob(STUDIO_PATIENTS.p0157.dob, "es")}.`,
      goalLabel: "Objetivo",
      goal: "Dolor fuerte desde ayer; quieres que te vean lo antes posible. Sin fiebre, sin hinchazón, sin dificultad para respirar ni sangrado.",
      hintsLabel: "Puede salir",
      hints: [
        "Describir el dolor y desde cuándo",
        "Responder a preguntas de triaje (fiebre, hinchazón, etc.)",
        "Aceptar la primera franja urgente que propongan",
      ],
    },
    price_question: {
      title: "Precios y horario",
      whoLabel: "Quién eres",
      who: "Sin identidad: solo tienes dudas generales sobre la clínica.",
      goalLabel: "Objetivo",
      goal: "Preguntar cuánto cuesta una limpieza, si la consulta de ortodoncia tiene precio fijo y cuál es el horario de apertura.",
      hintsLabel: "Puede salir",
      hints: [
        "Preguntar por limpieza dental",
        "Preguntar por consulta de ortodoncia",
        "Pedir horario de la clínica",
      ],
    },
  },
  en: {
    new_patient: {
      title: "New patient",
      whoLabel: "Who you are",
      who: `${fullName(NEW_PATIENT)}, born ${formatDob(NEW_PATIENT.dob, "en")}. Phone: ${NEW_PATIENT.phone}.`,
      goalLabel: "Goal",
      goal: "Register and book a first check-up.",
      hintsLabel: "Things that may come up",
      hints: [
        "Your full name and date of birth",
        "Reason: first visit / check-up",
        "Day preferences if asked",
      ],
    },
    earliest_checkup: {
      title: "Earliest check-up",
      whoLabel: "Who you are",
      who: `Known patient (${fullName(STUDIO_PATIENTS.p0097)}), born ${formatDob(STUDIO_PATIENTS.p0097.dob, "en")}.`,
      goalLabel: "Goal",
      goal: "The earliest check-up available with any dentist.",
      hintsLabel: "Things that may come up",
      hints: [
        "Confirm identity with name and date of birth",
        "Flexibility if several slots are offered",
      ],
    },
    move: {
      title: "Move appointment",
      whoLabel: "Who you are",
      who: `Known patient (${fullName(STUDIO_PATIENTS.p0181)}), born ${formatDob(STUDIO_PATIENTS.p0181.dob, "en")}.`,
      goalLabel: "Goal",
      goal: "Move your next appointment to a later week, in the afternoon. You do not remember the date—ask the receptionist.",
      hintsLabel: "Things that may come up",
      hints: [
        "Ask for the current appointment date and time",
        "Say you prefer afternoons, about a week later",
      ],
    },
    cancel: {
      title: "Cancel appointment",
      whoLabel: "Who you are",
      who: `Known patient (${fullName(STUDIO_PATIENTS.p0042)}), born ${formatDob(STUDIO_PATIENTS.p0042.dob, "en")}.`,
      goalLabel: "Goal",
      goal: "Cancel your next appointment. You do not remember the date—ask them to confirm it.",
      hintsLabel: "Things that may come up",
      hints: [
        "Confirm which appointment is being cancelled",
        "Thank them for confirming",
      ],
    },
    toothache: {
      title: "Toothache urgency",
      whoLabel: "Who you are",
      who: `Known patient (${fullName(STUDIO_PATIENTS.p0157)}), born ${formatDob(STUDIO_PATIENTS.p0157.dob, "en")}.`,
      goalLabel: "Goal",
      goal: "Strong pain since yesterday; you want to be seen as soon as possible. No fever, swelling, breathing trouble, or bleeding.",
      hintsLabel: "Things that may come up",
      hints: [
        "Describe the pain and when it started",
        "Answer triage questions (fever, swelling, etc.)",
        "Take the first urgent slot offered",
      ],
    },
    price_question: {
      title: "Prices and hours",
      whoLabel: "Who you are",
      who: "No identity needed—general questions about the clinic.",
      goalLabel: "Goal",
      goal: "Ask how much a cleaning costs, whether an orthodontics consultation has a fixed price, and opening hours.",
      hintsLabel: "Things that may come up",
      hints: [
        "Ask about dental cleaning",
        "Ask about orthodontics consultation",
        "Ask for opening hours",
      ],
    },
  },
};

export const STUDIO_CARD_IDS: StudioCardId[] = [
  "new_patient",
  "earliest_checkup",
  "move",
  "cancel",
  "toothache",
  "price_question",
];

export function getStudioCards(lang: Lang): StudioCardView[] {
  return STUDIO_CARD_IDS.map((id) => ({
    id,
    ...CONTENT[lang][id],
  }));
}