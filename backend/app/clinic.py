"""The clinic's catalogue, its dated calendar and the texts the agent may read out.

Everything here is read from the seeded database (`config.CLINIC_DB`, schema in
`engine/schema.sql`), opened read-only, and cached on the file's modification time
and size, so a rebuilt database is picked up and an unchanged one is read once.

No function in this module reads the wall clock (contracts §1.4): every date comes
from the `now` the call passes in, converted to the clinic's zone (contracts §2).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app import config

CLINIC_ZONE = ZoneInfo("Europe/Madrid")

LANGUAGES = ("es", "en")

TOPICS = ("hours", "location", "prices", "insurance", "treatments", "payment", "contact")

# --- demo texts for the topics with no table of their own -------------------
# Plainly fictional: this is a demo clinic. Kept short, because `clinic_info`
# hands the text to the brain, which says it in one or two spoken sentences.

LOCATION_TEXT = {
    "es": (
        "La clínica está en {address}. El catálogo no tiene datos de transporte"
        " público ni de aparcamiento, así que no los invente: dé la dirección y"
        " dígale al paciente que puede buscarla en una aplicación de mapas."
    ),
    "en": (
        "The clinic is at {address}. The catalogue holds no public-transport or"
        " parking details, so do not invent any: give the address and tell the"
        " patient they can look it up in a maps app."
    ),
}

INSURANCE_TEXT = {
    "es": (
        "Es una clínica privada y no factura directamente a las compañías de"
        " seguros. El paciente paga la visita y, con la factura, puede pedir el"
        " reembolso a su seguro si su póliza lo cubre."
    ),
    "en": (
        "It is a private clinic and does not bill insurers directly. The patient"
        " pays for the visit and can ask their insurer for a refund with the"
        " invoice, if their policy covers it."
    ),
}

PAYMENT_TEXT = {
    "es": "Se paga al final de la visita, con tarjeta o en efectivo.",
    "en": "Payment is at the end of the visit, by card or in cash.",
}

PRICE_ON_REQUEST = {
    "es": "el precio se da en la clínica",
    "en": "price given at the clinic",
}

ABSENCE_REASONS = {
    "annual_leave": {"es": "vacaciones", "en": "annual leave"},
    "sick_leave": {"es": "baja por enfermedad", "en": "sick leave"},
    "training": {"es": "formación", "en": "training"},
}

ROLE_WORDS = {
    "dentist": {"es": "dentista", "en": "dentist"},
    "hygienist": {"es": "higienista", "en": "hygienist"},
}

WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
WEEKDAYS_EN_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS_ES_SHORT = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MONTHS_EN_SHORT = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
MONTHS_EN = (
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
)
MONTHS_ES = (
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
)

DIGIT_WORDS = {
    "es": ("cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"),
    "en": ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"),
}

PLUS_WORD = {"es": "más", "en": "plus"}

SPAIN_CODE = "34"
SPAIN_NATIONAL_DIGITS = 9

# Calling codes read apart from the national number (contracts §6.1). A code that
# is not listed falls back to the first two digits, the commonest length.
COUNTRY_CODES = ("1", "33", "34", "39", "44", "49", "212", "351", "352", "376")


class CatalogueUnavailable(RuntimeError):
    """The clinic database is missing or cannot be read."""


@dataclass(frozen=True)
class Site:
    name: str
    address: str
    phone: str
    timezone: str


@dataclass(frozen=True)
class WorkingSpan:
    weekday: int  # 0 = Monday
    start: str  # HH:MM, clinic zone
    end: str


@dataclass(frozen=True)
class Practitioner:
    practitioner_id: str
    name: str
    role: str
    languages: tuple[str, ...]
    treatments: tuple[str, ...]
    hours: tuple[WorkingSpan, ...]

    def works_on(self, weekday: int) -> tuple[WorkingSpan, ...]:
        return tuple(h for h in self.hours if h.weekday == weekday)


@dataclass(frozen=True)
class Treatment:
    treatment_id: str
    name_es: str
    name_en: str
    minutes: int
    price_eur: int | None


@dataclass(frozen=True)
class Absence:
    practitioner_id: str
    start: date
    end: date
    reason: str

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True)
class Catalogue:
    site: Site
    practitioners: tuple[Practitioner, ...]
    treatments: tuple[Treatment, ...]
    closures: Mapping[date, str]
    absences: tuple[Absence, ...]

    def practitioner(self, practitioner_id: str) -> Practitioner | None:
        for p in self.practitioners:
            if p.practitioner_id == practitioner_id:
                return p
        return None

    def treatment(self, treatment_id: str) -> Treatment | None:
        for t in self.treatments:
            if t.treatment_id == treatment_id:
                return t
        return None

    def does_treatment(self, treatment_id: str) -> tuple[Practitioner, ...]:
        return tuple(p for p in self.practitioners if treatment_id in p.treatments)


# path -> (st_mtime_ns, st_size, catalogue). Never keyed on a date or a clock.
_CACHE: dict[str, tuple[int, int, Catalogue]] = {}


def load_catalogue(db_path: Path | None = None) -> Catalogue:
    """The catalogue of the seeded database, read once per version of the file."""
    path = Path(db_path or config.CLINIC_DB).resolve()
    try:
        stat = path.stat()
    except OSError as exc:
        msg = f"clinic database not found: {path} (run `make seed`)"
        raise CatalogueUnavailable(msg) from exc

    cached = _CACHE.get(str(path))
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]

    catalogue = _read_catalogue(path)
    _CACHE[str(path)] = (stat.st_mtime_ns, stat.st_size, catalogue)
    return catalogue


def _read_catalogue(path: Path) -> Catalogue:
    uri = f"file:{quote(str(path))}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - needs an unreadable file
        msg = f"cannot open clinic database read-only: {path}"
        raise CatalogueUnavailable(msg) from exc
    try:
        row = conn.execute("SELECT name, address, phone, timezone FROM site").fetchone()
        if row is None:
            msg = f"clinic database has no site row: {path}"
            raise CatalogueUnavailable(msg)
        site = Site(name=row[0], address=row[1], phone=row[2], timezone=row[3])

        treatments_of: dict[str, list[str]] = {}
        for pid, tid in conn.execute(
            "SELECT practitioner_id, treatment_id FROM practitioner_treatments"
            " ORDER BY practitioner_id, treatment_id"
        ):
            treatments_of.setdefault(pid, []).append(tid)

        hours_of: dict[str, list[WorkingSpan]] = {}
        for pid, weekday, start, end in conn.execute(
            "SELECT practitioner_id, weekday, start_local, end_local FROM working_hours"
            " ORDER BY practitioner_id, weekday, start_local"
        ):
            hours_of.setdefault(pid, []).append(WorkingSpan(int(weekday), start, end))

        practitioners = tuple(
            Practitioner(
                practitioner_id=pid,
                name=name,
                role=role,
                languages=tuple(part for part in languages.split(",") if part),
                treatments=tuple(treatments_of.get(pid, ())),
                hours=tuple(hours_of.get(pid, ())),
            )
            for pid, name, role, languages in conn.execute(
                "SELECT practitioner_id, name, role, languages FROM practitioners"
                " ORDER BY practitioner_id"
            )
        )

        treatments = tuple(
            Treatment(
                treatment_id=tid,
                name_es=name_es,
                name_en=name_en,
                minutes=int(minutes),
                price_eur=None if price is None else int(price),
            )
            for tid, name_es, name_en, minutes, price in conn.execute(
                "SELECT treatment_id, name_es, name_en, minutes, price_eur FROM treatments"
                " ORDER BY treatment_id"
            )
        )

        closures = {
            date.fromisoformat(day): name
            for day, name in conn.execute("SELECT date, name FROM closures ORDER BY date")
        }

        absences = tuple(
            Absence(
                practitioner_id=pid,
                start=date.fromisoformat(start),
                end=date.fromisoformat(end),
                reason=reason,
            )
            for pid, start, end, reason in conn.execute(
                "SELECT practitioner_id, start_date, end_date, reason FROM absences"
                " ORDER BY start_date, practitioner_id"
            )
        )
    except (sqlite3.Error, ValueError) as exc:
        msg = f"cannot read clinic database: {path}"
        raise CatalogueUnavailable(msg) from exc
    finally:
        conn.close()

    return Catalogue(
        site=site,
        practitioners=practitioners,
        treatments=treatments,
        closures=MappingProxyType(closures),
        absences=absences,
    )


# --- the clinic's phone number, said digit by digit -------------------------


def spoken_phone(phone: str, lang: str) -> str:
    """An E.164 number as the agent and the clips say it (contracts §6.1).

    A Spanish number loses its country code and is read in three groups of three;
    any other number keeps its code ("plus four four") before its national digits,
    read in groups of three or four.
    """
    words = _digit_words(lang)
    digits = re.sub(r"\D", "", phone)
    if not digits:
        msg = f"phone number has no digits: {phone!r}"
        raise ValueError(msg)

    code, national = _split_country_code(digits)
    groups: list[str] = []
    if code != SPAIN_CODE or len(national) != SPAIN_NATIONAL_DIGITS:
        if code:
            groups.append(PLUS_WORD[lang] + " " + " ".join(words[int(d)] for d in code))
        else:
            national = digits
    for size in _group_sizes(len(national)):
        chunk, national = national[:size], national[size:]
        groups.append(" ".join(words[int(d)] for d in chunk))
    return ", ".join(groups)


def _digit_words(lang: str) -> tuple[str, ...]:
    if lang not in DIGIT_WORDS:
        msg = f"unknown language: {lang!r} (expected one of {', '.join(LANGUAGES)})"
        raise ValueError(msg)
    return DIGIT_WORDS[lang]


def _split_country_code(digits: str) -> tuple[str, str]:
    for code in sorted(COUNTRY_CODES, key=len, reverse=True):
        if digits.startswith(code):
            return code, digits[len(code) :]
    return digits[:2], digits[2:]


def _group_sizes(count: int) -> list[int]:
    """Group sizes of three or four digits, the longer groups last."""
    if count <= 4:
        return [count]
    threes, rest = divmod(count, 3)
    if rest and threes < rest:
        return [3] * threes + [rest]
    sizes = [3] * threes
    for i in range(rest):
        sizes[threes - 1 - i] += 1
    return sizes


# --- opening hours ---------------------------------------------------------


def opening_hours(catalogue: Catalogue) -> dict[int, tuple[tuple[str, str], ...]]:
    """The clinic's open spans per weekday: the union of the practitioners' hours."""
    per_day: dict[int, list[list[int]]] = {}
    for practitioner in catalogue.practitioners:
        for span in practitioner.hours:
            per_day.setdefault(span.weekday, []).append([_minutes(span.start), _minutes(span.end)])
    merged: dict[int, tuple[tuple[str, str], ...]] = {}
    for weekday, spans in per_day.items():
        runs: list[list[int]] = []
        for start, end in sorted(spans):
            if runs and start <= runs[-1][1]:
                runs[-1][1] = max(runs[-1][1], end)
            else:
                runs.append([start, end])
        merged[weekday] = tuple((_hhmm(a), _hhmm(b)) for a, b in runs)
    return merged


def _minutes(hhmm: str) -> int:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return hours * 60 + minutes


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _span_text(spans: tuple[tuple[str, str], ...], lang: str) -> str:
    if not spans:
        return "cerrado" if lang == "es" else "closed"
    if lang == "es":
        return " y ".join(f"de {start} a {end}" for start, end in spans)
    return " and ".join(f"{start}-{end}" for start, end in spans)


def _weekday_runs(
    spans_by_weekday: dict[int, tuple[tuple[str, str], ...]], weekdays: range
) -> list[tuple[int, int, tuple[tuple[str, str], ...]]]:
    """Runs of consecutive weekdays that share the same spans."""
    runs: list[tuple[int, int, tuple[tuple[str, str], ...]]] = []
    for weekday in weekdays:
        spans = spans_by_weekday.get(weekday, ())
        if runs and runs[-1][2] == spans:
            runs[-1] = (runs[-1][0], weekday, spans)
        else:
            runs.append((weekday, weekday, spans))
    return runs


def _week_text(
    spans_by_weekday: dict[int, tuple[tuple[str, str], ...]],
    lang: str,
    *,
    short: bool = False,
    include_closed: bool = True,
) -> str:
    if lang == "es":
        names = WEEKDAYS_ES_SHORT if short else WEEKDAYS_ES
    else:
        names = WEEKDAYS_EN_SHORT if short else WEEKDAYS_EN
    parts: list[str] = []
    for first, last, spans in _weekday_runs(spans_by_weekday, range(7)):
        if not spans and not include_closed:
            continue
        if first == last:
            days = names[first]
        elif lang == "es":
            days = (
                f"de {names[first]} a {names[last]}"
                if not short
                else f"{names[first]}-{names[last]}"
            )
        else:
            days = (
                f"{names[first]} to {names[last]}" if not short else f"{names[first]}-{names[last]}"
            )
        parts.append(f"{days} {_span_text(spans, lang)}")
    return "; ".join(parts)


# --- the dated calendar ----------------------------------------------------


def calendar_text(now: datetime, catalogue: Catalogue | None, days: int = 14) -> str:
    """One line per day, from today to `days` days ahead, with the clinic's status.

    The model never works out a weekday itself: each line carries both weekday
    names, the day, month and year, and the ISO date.
    """
    today = now.astimezone(CLINIC_ZONE).date()
    local_now = now.astimezone(CLINIC_ZONE)
    last = today + timedelta(days=days)
    lines = [
        "CALENDAR (clinic time zone: Europe/Madrid). Read weekdays and dates from here,"
        " never work them out yourself.",
        f"Right now it is {WEEKDAYS_EN[today.weekday()]} {today.day}"
        f" {MONTHS_EN[today.month - 1]} {today.year} ({WEEKDAYS_ES[today.weekday()]}"
        f" {today.day} de {MONTHS_ES[today.month - 1]}), {local_now.strftime('%H:%M')},"
        f" ISO {local_now.isoformat(timespec='minutes')}.",
        f"The calendar covers today and the next {days} days, up to {last.isoformat()}."
        " For a later date, work from the date the caller gives you and ask them when"
        " you are not sure; never guess a weekday beyond this list.",
    ]
    for offset in range(days + 1):
        day = today + timedelta(days=offset)
        lines.append(_calendar_line(day, offset, catalogue))
    return "\n".join(lines)


_DAY_LABELS = (
    "today / hoy",
    "tomorrow / mañana",
    "the day after tomorrow / pasado mañana",
)


def _calendar_line(day: date, offset: int, catalogue: Catalogue | None) -> str:
    head = (
        f"- {WEEKDAYS_EN_SHORT[day.weekday()]} {day.day} {MONTHS_EN_SHORT[day.month - 1]}"
        f" {day.year} ({WEEKDAYS_ES[day.weekday()]} {day.day} de"
        f" {MONTHS_ES[day.month - 1]}), {day.isoformat()}"
    )
    if offset < len(_DAY_LABELS):
        head += f", {_DAY_LABELS[offset]}"
    if catalogue is None:
        return head
    return f"{head}: {_day_status(day, catalogue)}"


def _day_status(day: date, catalogue: Catalogue) -> str:
    holiday = catalogue.closures.get(day)
    if holiday is not None:
        return f"closed, holiday: {holiday}"

    working = [p for p in catalogue.practitioners if p.works_on(day.weekday())]
    if not working:
        return f"closed, nobody works on {WEEKDAYS_EN[day.weekday()]}"

    # Only the people who work that weekday can be away that day.
    away: list[str] = []
    away_ids: set[str] = set()
    working_ids = {p.practitioner_id for p in working}
    for absence in catalogue.absences:
        practitioner = catalogue.practitioner(absence.practitioner_id)
        if practitioner is None or practitioner.practitioner_id not in working_ids:
            continue
        if not absence.covers(day):
            continue
        away.append(f"{practitioner.name} ({_absence_words(absence.reason, 'en')})")
        away_ids.add(practitioner.practitioner_id)
    present = [p for p in working if p.practitioner_id not in away_ids]
    if not present:
        return "closed, everyone who works that day is away: " + ", ".join(away)

    status = "open"
    if day.weekday() == 5 and len(working) < len(catalogue.practitioners):
        who = ", ".join(
            f"{p.name} {_span_text(tuple((s.start, s.end) for s in p.works_on(5)), 'en')}"
            for p in present
        )
        status = f"open, Saturday staff only: {who}"
    if away:
        status += "; away: " + ", ".join(away)
    return status


def _absence_words(reason: str, lang: str) -> str:
    words = ABSENCE_REASONS.get(reason)
    if words is None:
        return reason.replace("_", " ")
    return words[lang]


# --- the texts the agent may read out --------------------------------------


def topic_text(topic: str, lang: str, catalogue: Catalogue) -> str:
    """Catalogue text for one `clinic_info` topic (contracts §5.3), and nothing else."""
    if lang not in LANGUAGES:
        msg = f"unknown language: {lang!r} (expected one of {', '.join(LANGUAGES)})"
        raise ValueError(msg)
    if topic not in TOPICS:
        msg = f"unknown topic: {topic!r} (expected one of {', '.join(TOPICS)})"
        raise ValueError(msg)
    builder = _TOPIC_BUILDERS[topic]
    return builder(lang, catalogue)


def _hours_topic(lang: str, catalogue: Catalogue) -> str:
    week = _week_text(opening_hours(catalogue), lang)
    if lang == "es":
        return f"Horario de {catalogue.site.name}: {week}. Los festivos cierra."
    return f"Opening hours of {catalogue.site.name}: {week}. It closes on public holidays."


def _location_topic(lang: str, catalogue: Catalogue) -> str:
    return LOCATION_TEXT[lang].format(address=catalogue.site.address)


def _prices_topic(lang: str, catalogue: Catalogue) -> str:
    items = []
    for treatment in catalogue.treatments:
        name = treatment.name_es if lang == "es" else treatment.name_en
        if treatment.price_eur is None:
            items.append(f"{name}: {PRICE_ON_REQUEST[lang]}")
        else:
            items.append(f"{name}: {treatment.price_eur} euros")
    joined = "; ".join(items)
    if lang == "es":
        return f"Precios: {joined}. Son los precios del catálogo, sin descuentos ni seguros."
    return f"Prices: {joined}. These are the catalogue prices, with no discounts or insurance."


def _treatments_topic(lang: str, catalogue: Catalogue) -> str:
    items = []
    for treatment in catalogue.treatments:
        name = treatment.name_es if lang == "es" else treatment.name_en
        unit = "minutos" if lang == "es" else "minutes"
        items.append(f"{name} ({treatment.minutes} {unit})")
    joined = "; ".join(items)
    if lang == "es":
        return f"Tratamientos de la clínica: {joined}."
    return f"Treatments the clinic offers: {joined}."


def _insurance_topic(lang: str, _catalogue: Catalogue) -> str:
    return INSURANCE_TEXT[lang]


def _payment_topic(lang: str, _catalogue: Catalogue) -> str:
    return PAYMENT_TEXT[lang]


def _contact_topic(lang: str, catalogue: Catalogue) -> str:
    spoken = spoken_phone(catalogue.site.phone, lang)
    if lang == "es":
        return (
            f"Teléfono de {catalogue.site.name}: {spoken}."
            f" La clínica está en {catalogue.site.address}."
            " Diga el número dígito a dígito, tal como está escrito aquí."
        )
    return (
        f"Phone number of {catalogue.site.name}: {spoken}."
        f" The clinic is at {catalogue.site.address}."
        " Say the number digit by digit, exactly as written here."
    )


_TOPIC_BUILDERS = {
    "hours": _hours_topic,
    "location": _location_topic,
    "prices": _prices_topic,
    "insurance": _insurance_topic,
    "treatments": _treatments_topic,
    "payment": _payment_topic,
    "contact": _contact_topic,
}


def catalogue_text(catalogue: Catalogue, lang: str = "en") -> str:
    """The compact catalogue block of the brain prompt (contracts §4.2)."""
    if lang not in LANGUAGES:
        msg = f"unknown language: {lang!r} (expected one of {', '.join(LANGUAGES)})"
        raise ValueError(msg)
    words = _CATALOGUE_WORDS[lang]
    site = catalogue.site
    week = _week_text(opening_hours(catalogue), lang, short=True)
    lines = [
        words["title"],
        f"{words['name']}: {site.name}",
        f"{words['address']}: {site.address}",
        f"{words['phone_es']}: {spoken_phone(site.phone, 'es')}",
        f"{words['phone_en']}: {spoken_phone(site.phone, 'en')}",
        f"{words['hours']}: {week}",
        "",
        words["treatments"],
    ]
    for treatment in catalogue.treatments:
        price = (
            PRICE_ON_REQUEST[lang] if treatment.price_eur is None else f"{treatment.price_eur} EUR"
        )
        who = ", ".join(p.practitioner_id for p in catalogue.does_treatment(treatment.treatment_id))
        lines.append(
            f"- {treatment.treatment_id} | {treatment.name_es} / {treatment.name_en}"
            f" | {treatment.minutes} min | {price} | {who or '-'}"
        )

    lines += ["", words["practitioners"]]
    for practitioner in catalogue.practitioners:
        spans = {
            weekday: tuple((s.start, s.end) for s in practitioner.works_on(weekday))
            for weekday in range(7)
        }
        hours = _week_text(spans, lang, short=True, include_closed=False)
        role = ROLE_WORDS.get(practitioner.role, {}).get(lang, practitioner.role)
        lines.append(
            f"- {practitioner.practitioner_id} | {practitioner.name} | {role}"
            f" | {', '.join(practitioner.languages)} | {hours}"
        )

    lines += [
        "",
        f"{words['insurance']}: {INSURANCE_TEXT[lang]}",
        f"{words['payment']}: {PAYMENT_TEXT[lang]}",
    ]
    return "\n".join(lines)


_CATALOGUE_WORDS = {
    "en": {
        "title": "THE CLINIC (the only source of catalogue facts)",
        "name": "Name",
        "address": "Address",
        "phone_es": "Phone, said in Spanish",
        "phone_en": "Phone, said in English",
        "hours": "Opening hours",
        "treatments": "Treatments (id | names es/en | minutes | price | who does it):",
        "practitioners": (
            "Practitioners (id | name, say it exactly as written | role | languages |"
            " weekly hours); who is away on a given day is in the calendar above:"
        ),
        "insurance": "Insurance",
        "payment": "Payment",
    },
    "es": {
        "title": "LA CLÍNICA (el único origen de los datos del catálogo)",
        "name": "Nombre",
        "address": "Dirección",
        "phone_es": "Teléfono, dicho en español",
        "phone_en": "Teléfono, dicho en inglés",
        "hours": "Horario",
        "treatments": "Tratamientos (id | nombres es/en | minutos | precio | quién lo hace):",
        "practitioners": (
            "Profesionales (id | nombre, dígalo tal como está escrito | puesto | idiomas |"
            " horario semanal); quién falta cada día está en el calendario de arriba:"
        ),
        "insurance": "Seguros",
        "payment": "Pago",
    },
}
