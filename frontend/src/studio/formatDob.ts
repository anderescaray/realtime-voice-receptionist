import type { Lang } from "../i18n";

const MONTHS_ES = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
] as const;

const MONTHS_EN = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

/** Format YYYY-MM-DD in the visitor's language without Date timezone shifts. */
export function formatDob(iso: string, lang: Lang): string {
  const [yearStr, monthStr, dayStr] = iso.split("-");
  const year = Number(yearStr);
  const month = Number(monthStr);
  const day = Number(dayStr);
  if (lang === "es") {
    return `${day} de ${MONTHS_ES[month - 1]} de ${year}`;
  }
  return `${day} ${MONTHS_EN[month - 1]} ${year}`;
}

export function fullName(
  person: { firstName: string; lastName1: string; lastName2: string },
): string {
  return `${person.firstName} ${person.lastName1} ${person.lastName2}`;
}