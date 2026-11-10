"""Static clinic catalogue: site, staff, treatments, hours, closures and absences."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TypedDict

from seed import phones

TREATMENT_IDS = (
    "checkup",
    "cleaning",
    "filling",
    "extraction",
    "ortho_consult",
    "urgency",
)

SITE = {
    "site_id": "main",
    "name": "Clínica Dental Los Tilos",
    "address": "Calle de los Tilos 14, 28000 Madrid",
    "phone": phones.SITE_PHONE,
    "timezone": "Europe/Madrid",
}

PRACTITIONERS: tuple[dict[str, str], ...] = (
    {
        "practitioner_id": "dent_1",
        "name": "Dra. Carmen Iglesias Roldán",
        "role": "dentist",
        "languages": "es,en",
    },
    {
        "practitioner_id": "dent_2",
        "name": "Dr. Javier Moya Navarro",
        "role": "dentist",
        "languages": "es",
    },
    {
        "practitioner_id": "dent_3",
        "name": "Dra. Lucía Ferrer Castillo",
        "role": "dentist",
        "languages": "es,en",
    },
    {
        "practitioner_id": "dent_4",
        "name": "Dr. Andrés Peña Salazar",
        "role": "dentist",
        "languages": "es",
    },
    {
        "practitioner_id": "hyg_1",
        "name": "Elena Torres Vidal",
        "role": "hygienist",
        "languages": "es",
    },
)

TREATMENTS: tuple[dict[str, object], ...] = (
    {
        "treatment_id": "checkup",
        "name_es": "Revisión",
        "name_en": "Check-up",
        "minutes": 30,
        "price_eur": 40,
    },
    {
        "treatment_id": "cleaning",
        "name_es": "Limpieza",
        "name_en": "Cleaning",
        "minutes": 45,
        "price_eur": 60,
    },
    {
        "treatment_id": "filling",
        "name_es": "Empaste",
        "name_en": "Filling",
        "minutes": 45,
        "price_eur": 70,
    },
    {
        "treatment_id": "extraction",
        "name_es": "Extracción",
        "name_en": "Extraction",
        "minutes": 60,
        "price_eur": 90,
    },
    {
        "treatment_id": "ortho_consult",
        "name_es": "Consulta de ortodoncia",
        "name_en": "Orthodontics consultation",
        "minutes": 30,
        "price_eur": None,
    },
    {
        "treatment_id": "urgency",
        "name_es": "Urgencia",
        "name_en": "Urgent visit",
        "minutes": 30,
        "price_eur": 50,
    },
)

PRACTITIONER_TREATMENTS: tuple[tuple[str, str], ...] = (
    ("dent_1", "checkup"),
    ("dent_1", "filling"),
    ("dent_1", "urgency"),
    ("dent_1", "extraction"),
    ("dent_1", "cleaning"),
    ("dent_2", "checkup"),
    ("dent_2", "filling"),
    ("dent_2", "urgency"),
    ("dent_2", "extraction"),
    ("dent_3", "checkup"),
    ("dent_3", "filling"),
    ("dent_3", "urgency"),
    ("dent_3", "ortho_consult"),
    ("dent_4", "checkup"),
    ("dent_4", "filling"),
    ("dent_4", "urgency"),
    ("hyg_1", "cleaning"),
)

# weekday 0 = Monday; start_local/end_local as HH:MM on 15-minute grid
WORKING_HOURS: tuple[tuple[str, int, str, str], ...] = (
    ("dent_1", 0, "09:00", "14:00"),
    ("dent_1", 0, "16:00", "20:00"),
    ("dent_1", 1, "09:00", "14:00"),
    ("dent_1", 1, "16:00", "20:00"),
    ("dent_1", 2, "09:00", "14:00"),
    ("dent_1", 2, "16:00", "20:00"),
    ("dent_1", 3, "09:00", "14:00"),
    ("dent_1", 3, "16:00", "20:00"),
    ("dent_1", 4, "09:00", "14:00"),
    ("dent_1", 4, "16:00", "20:00"),
    ("dent_2", 0, "08:00", "14:00"),
    ("dent_2", 1, "08:00", "14:00"),
    ("dent_2", 2, "08:00", "14:00"),
    ("dent_2", 3, "08:00", "14:00"),
    ("dent_2", 4, "08:00", "14:00"),
    ("dent_2", 5, "09:00", "14:00"),
    ("dent_3", 0, "14:00", "20:00"),
    ("dent_3", 1, "14:00", "20:00"),
    ("dent_3", 2, "14:00", "20:00"),
    ("dent_3", 3, "14:00", "20:00"),
    ("dent_3", 4, "14:00", "20:00"),
    ("dent_4", 0, "10:00", "19:00"),
    ("dent_4", 1, "10:00", "19:00"),
    ("dent_4", 2, "10:00", "19:00"),
    ("dent_4", 3, "10:00", "19:00"),
    ("hyg_1", 0, "09:00", "17:00"),
    ("hyg_1", 1, "09:00", "17:00"),
    ("hyg_1", 2, "09:00", "17:00"),
    ("hyg_1", 3, "09:00", "17:00"),
    ("hyg_1", 4, "09:00", "17:00"),
)

CLOSURES: tuple[tuple[str, str], ...] = (
    ("2026-01-01", "Año Nuevo"),
    ("2026-01-06", "Epifanía del Señor"),
    ("2026-05-01", "Fiesta del Trabajo"),
    ("2026-05-02", "Fiesta de la Comunidad de Madrid"),
    ("2026-08-15", "Asunción de la Virgen"),
    ("2026-10-12", "Fiesta Nacional de España"),
    ("2026-11-01", "Todos los Santos"),
    ("2026-11-09", "Fiesta de la Almudena"),
    ("2026-12-06", "Día de la Constitución"),
    ("2026-12-08", "Inmaculada Concepción"),
    ("2026-12-25", "Navidad"),
    ("2027-01-01", "Año Nuevo"),
    ("2027-01-06", "Epifanía del Señor"),
    ("2027-05-01", "Fiesta del Trabajo"),
    ("2027-05-02", "Fiesta de la Comunidad de Madrid"),
    ("2027-08-15", "Asunción de la Virgen"),
    ("2027-10-12", "Fiesta Nacional de España"),
    ("2027-11-01", "Todos los Santos"),
    ("2027-11-09", "Fiesta de la Almudena"),
    ("2027-12-06", "Día de la Constitución"),
    ("2027-12-08", "Inmaculada Concepción"),
    ("2027-12-25", "Navidad"),
)

CLOSURE_DATES: frozenset[str] = frozenset(d for d, _ in CLOSURES)


class AbsenceRow(TypedDict):
    practitioner_id: str
    start_date: str
    end_date: str
    reason: str


def _iso(d: date) -> str:
    return d.isoformat()


def absences_for_anchor(anchor: date) -> tuple[AbsenceRow, ...]:
    return (
        {
            "practitioner_id": "dent_2",
            "start_date": _iso(anchor + timedelta(days=14)),
            "end_date": _iso(anchor + timedelta(days=18)),
            "reason": "annual_leave",
        },
        {
            "practitioner_id": "dent_4",
            "start_date": _iso(anchor + timedelta(days=9)),
            "end_date": _iso(anchor + timedelta(days=9)),
            "reason": "training",
        },
        {
            "practitioner_id": "hyg_1",
            "start_date": _iso(anchor + timedelta(days=2)),
            "end_date": _iso(anchor + timedelta(days=3)),
            "reason": "sick_leave",
        },
    )


TREATMENT_MINUTES: dict[str, int] = {str(t["treatment_id"]): int(t["minutes"]) for t in TREATMENTS}
