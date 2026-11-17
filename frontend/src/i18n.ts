export type Lang = "es" | "en";

const es = {
  productName: "clinic-voice-agent",
  navLanding: "Inicio",
  navStudio: "Estudio",
  langSwitchLabel: "Idioma",
  landingTitle: "Recepción por voz para Clínica Dental Los Tilos",
  landingLead:
    "Demostración de un recepcionista virtual para la clínica ficticia Los Tilos, en Madrid. Atiende en español e inglés, ayuda a pacientes nuevos y conocidos y consulta en vivo los huecos libres de la agenda de prueba.",
  landingPoint1:
    "Identifica a quien llama por nombre y fecha de nacimiento y propone huecos libres verificados.",
  landingPoint2:
    "Reserva, cambia y anula citas; atiende urgencias por dolor y responde preguntas sobre precios y horario.",
  landingPoint3:
    "En esta página puedes llamar con un guion de prueba o elegir un rol concreto en el Estudio.",
  landingStudioLink: "Probar un rol en el Estudio",
  studioTitle: "Estudio de roles",
  studioLead:
    "Elige un escenario. Durante la llamada verás aquí el personaje y el objetivo para que puedas improvisar con datos inventados, nunca los tuyos reales.",
  studioPickRole: "Selecciona una tarjeta para abrir la llamada de ese rol.",
  callNoticeRecorded:
    "Esta llamada se graba y se transcribe para poder revisarla.",
  callNoticeAi: "Hablarás con un asistente de inteligencia artificial, no con una persona.",
  callNoticeRetention:
    "La grabación y la transcripción se conservan {days} días y después se eliminan.",
  callNoticeRetentionOne:
    "La grabación y la transcripción se conservan 1 día y después se eliminan.",
  callNoticePrivacy:
    "No des tu nombre real, datos de salud ni teléfono. Usa los datos inventados de una tarjeta del Estudio.",
  callNoticePrivacyLanding:
    "No des tu nombre real, datos de salud ni teléfono. Usa los datos inventados de una tarjeta del {studio}.",
  callNoticeCap: "La llamada tiene un máximo de 5 minutos.",
  callUnderstand: "Entiendo y quiero continuar",
  callTalk: "Iniciar llamada",
  callHangUp: "Colgar",
  callStatusIdle: "Listo para llamar cuando marques el botón.",
  callStatusRequestingMic: "Solicitando acceso al micrófono…",
  callStatusConnecting: "Conectando con la clínica…",
  callStatusLive: "Llamada en curso. Puedes hablar.",
  callStatusEnded: "Llamada finalizada.",
  callStatusMicDenied: "No se pudo usar el micrófono. Revisa los permisos del navegador.",
  callStatusServerError: "El servidor no pudo atender la llamada. Inténtalo más tarde.",
  callStatusConnectionFailed: "No se pudo establecer la conexión.",
  callStatusNoMedia:
    "Tu navegador no permite usar el micrófono aquí (contexto no seguro o navegador antiguo). Abre la página por HTTPS o actualiza el navegador.",
  callSpeakingYou: "Tú",
  callSpeakingAgent: "Recepcionista",
  callElapsed: "Tiempo",
  callTapToHear: "Pulsa para oír al recepcionista",
  clinicName: "Clínica Dental Los Tilos · Madrid",
} as const;

const en: { [K in keyof typeof es]: string } = {
  productName: "clinic-voice-agent",
  navLanding: "Home",
  navStudio: "Studio",
  langSwitchLabel: "Language",
  landingTitle: "Voice reception for Clínica Dental Los Tilos",
  landingLead:
    "A demo virtual receptionist for the fictional Los Tilos dental clinic in Madrid. It speaks Spanish and English, helps new and returning patients, and checks live availability in the clinic's demo diary.",
  landingPoint1:
    "Recognises callers by name and date of birth and offers verified free slots.",
  landingPoint2:
    "Books, moves and cancels appointments; handles urgent toothache and answers questions about prices and hours.",
  landingPoint3:
    "On this site you can place a free test call or pick a scripted role in the Studio.",
  landingStudioLink: "Try a role in the Studio",
  studioTitle: "Role studio",
  studioLead:
    "Pick a scenario. During the call the character and goal stay visible here so you can improvise with made-up details—not your real ones.",
  studioPickRole: "Select a card to start the call for that role.",
  callNoticeRecorded: "This call is recorded and transcribed so it can be reviewed.",
  callNoticeAi: "You will talk to an AI assistant, not a human.",
  callNoticeRetention:
    "The recording and transcript are kept for {days} days, then deleted.",
  callNoticeRetentionOne: "The recording and transcript are kept for 1 day, then deleted.",
  callTapToHear: "Tap to hear the receptionist",
  callNoticePrivacy:
    "Do not give your real name, health details or phone number. Use the made-up details from a Studio card.",
  callNoticePrivacyLanding:
    "Do not give your real name, health details or phone number. Use the made-up details from a {studio} card.",
  callNoticeCap: "Calls are capped at 5 minutes.",
  callUnderstand: "I understand and want to continue",
  callTalk: "Start call",
  callHangUp: "Hang up",
  callStatusIdle: "Ready when you press the button.",
  callStatusRequestingMic: "Requesting microphone access…",
  callStatusConnecting: "Connecting to the clinic…",
  callStatusLive: "Call live. You may speak.",
  callStatusEnded: "Call ended.",
  callStatusMicDenied: "Microphone access was denied. Check your browser permissions.",
  callStatusServerError: "The server could not place the call. Try again later.",
  callStatusConnectionFailed: "The connection could not be established.",
  callStatusNoMedia:
    "This browser cannot use the microphone here (insecure context or outdated browser). Open the page over HTTPS or update your browser.",
  callSpeakingYou: "You",
  callSpeakingAgent: "Receptionist",
  callElapsed: "Elapsed",
  clinicName: "Clínica Dental Los Tilos · Madrid",
};

export type MessageKey = keyof typeof es;

export const messages = { es, en } satisfies Record<Lang, Record<MessageKey, string>>;

export function t(lang: Lang, key: MessageKey): string {
  return messages[lang][key];
}

export function defaultLangFromBrowser(): Lang {
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("en")) {
    return "en";
  }
  return "es";
}

export function langFromSearch(search: string): Lang | null {
  const params = new URLSearchParams(search);
  const lang = params.get("lang");
  if (lang === "es" || lang === "en") return lang;
  return null;
}

const STORAGE_KEY = "clinic-voice-agent-lang";

export function readStoredLang(): Lang | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "es" || v === "en") return v;
  } catch {
    /* ignore */
  }
  return null;
}

export function writeStoredLang(lang: Lang): void {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* ignore */
  }
}

export function resolveInitialLang(): Lang {
  const fromUrl =
    typeof window !== "undefined" ? langFromSearch(window.location.search) : null;
  if (fromUrl) return fromUrl;
  const stored = readStoredLang();
  if (stored) return stored;
  return defaultLangFromBrowser();
}

export function parseReviewDays(raw: string | undefined, fallback = 7): number {
  const n = Number.parseInt(raw ?? "", 10);
  if (Number.isFinite(n) && n > 0) return n;
  return fallback;
}

/** VITE_REVIEW_LINK_DAYS must be set at build time to match the server's REVIEW_LINK_DAYS (default 7 on both). */
export function reviewDaysFromEnv(): number {
  return parseReviewDays(import.meta.env.VITE_REVIEW_LINK_DAYS as string | undefined);
}