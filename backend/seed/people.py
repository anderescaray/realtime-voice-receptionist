"""Deterministic synthetic patients."""

from __future__ import annotations

import random
from typing import Any

from seed import phones

PATIENT_COUNT = 300

GIVEN_NAMES_ES = (
    "Ángela",
    "Antonio",
    "Beatriz",
    "Carlos",
    "Cristina",
    "Daniel",
    "Elena",
    "Fernando",
    "Gabriela",
    "Héctor",
    "Inés",
    "Javier",
    "Laura",
    "Manuel",
    "María",
    "Miguel",
    "Natalia",
    "Óscar",
    "Paula",
    "Raúl",
    "Sandra",
    "Teresa",
    "Víctor",
    "Yolanda",
    "Adrián",
    "Belén",
    "César",
    "Diana",
    "Emilio",
    "Francisco",
    "Gloria",
    "Hugo",
    "Isabel",
    "Jorge",
    "Lucía",
    "Marcos",
    "Noelia",
    "Pablo",
    "Rocío",
    "Sergio",
)

SURNAMES = (
    "García",
    "Fernández",
    "González",
    "Rodríguez",
    "López",
    "Martínez",
    "Sánchez",
    "Pérez",
    "Gómez",
    "Martín",
    "Jiménez",
    "Ruiz",
    "Hernández",
    "Díaz",
    "Moreno",
    "Álvarez",
    "Muñoz",
    "Romero",
    "Alonso",
    "Gutiérrez",
    "Navarro",
    "Torres",
    "Domínguez",
    "Vázquez",
    "Ramos",
    "Gil",
    "Ramírez",
    "Serrano",
    "Blanco",
    "Molina",
    "Morales",
    "Suárez",
    "Ortega",
    "Delgado",
    "Castro",
    "Ortiz",
    "Rubio",
    "Marín",
    "Iglesias",
    "Núñez",
    "Medina",
    "Garrido",
    "Cortés",
    "Castillo",
    "Santos",
    "Lozano",
    "Guerrero",
    "Cano",
    "Prieto",
    "Méndez",
    "Cruz",
    "Calvo",
    "Gallego",
    "Vidal",
    "León",
    "Herrera",
    "Márquez",
    "Peña",
    "Flores",
)

GIVEN_NAMES_FOREIGN = (
    "James",
    "Emily",
    "Michael",
    "Sarah",
    "David",
    "Anna",
    "Robert",
    "Lisa",
)

SURNAMES_FOREIGN = (
    "Smith",
    "Johnson",
    "Brown",
    "Taylor",
    "Wilson",
    "Davies",
    "Clark",
    "Walker",
)

FIXTURE_NAMESAKES = ("p0296", "p0297")
FIXTURE_SAME_FIRST_SURNAME = ("p0298", "p0299")
FIXTURE_ACCENTED = "p0300"


def _dob_from_rng(rng: random.Random) -> str:
    y = rng.randint(1940, 2018)
    m = rng.randint(1, 12)
    days_in_month = (
        31,
        29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    )
    d = rng.randint(1, days_in_month[m - 1])
    return f"{y:04d}-{m:02d}-{d:02d}"


def generate_patients(seed: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []

    # p0001..p0295
    for i in range(1, 296):
        pid = f"p{i:04d}"
        is_foreign = i <= 24  # ~8% of 295 ≈ 24 fixed slots for determinism
        if is_foreign:
            first = rng.choice(GIVEN_NAMES_FOREIGN)
            last1 = rng.choice(SURNAMES_FOREIGN)
            last2 = None
            lang = "en"
        else:
            first = rng.choice(GIVEN_NAMES_ES)
            last1 = rng.choice(SURNAMES)
            if rng.random() < 0.92:
                last2 = rng.choice(SURNAMES)
                while last2 == last1:
                    last2 = rng.choice(SURNAMES)
            else:
                last2 = None
            lang = "en" if rng.random() < 0.05 else "es"
        rows.append(
            {
                "patient_id": pid,
                "first_name": first,
                "last_name1": last1,
                "last_name2": last2,
                "dob": _dob_from_rng(rng),
                "phone": phones.patient_phone(i - 1),
                "language": lang,
                "created_by_call": None,
            }
        )

    rows.append(
        {
            "patient_id": FIXTURE_NAMESAKES[0],
            "first_name": "María",
            "last_name1": "García",
            "last_name2": "López",
            "dob": "1985-03-12",
            "phone": phones.patient_phone(295),
            "language": "es",
            "created_by_call": None,
        }
    )
    rows.append(
        {
            "patient_id": FIXTURE_NAMESAKES[1],
            "first_name": "María",
            "last_name1": "García",
            "last_name2": "López",
            "dob": "1992-07-28",
            "phone": phones.patient_phone(296),
            "language": "es",
            "created_by_call": None,
        }
    )
    rows.append(
        {
            "patient_id": FIXTURE_SAME_FIRST_SURNAME[0],
            "first_name": "Carlos",
            "last_name1": "Ruiz",
            "last_name2": "Mendoza",
            "dob": "1978-11-05",
            "phone": phones.patient_phone(297),
            "language": "es",
            "created_by_call": None,
        }
    )
    rows.append(
        {
            "patient_id": FIXTURE_SAME_FIRST_SURNAME[1],
            "first_name": "Carlos",
            "last_name1": "Ruiz",
            "last_name2": "Herrera",
            "dob": "2001-04-19",
            "phone": phones.patient_phone(298),
            "language": "es",
            "created_by_call": None,
        }
    )
    rows.append(
        {
            "patient_id": FIXTURE_ACCENTED,
            "first_name": "Ángela",
            "last_name1": "de la Fuente",
            "last_name2": "Márquez",
            "dob": "1967-09-30",
            "phone": phones.patient_phone(299),
            "language": "es",
            "created_by_call": None,
        }
    )

    fixtures = {
        "accented": FIXTURE_ACCENTED,
        "namesakes": list(FIXTURE_NAMESAKES),
        "same_first_surname": list(FIXTURE_SAME_FIRST_SURNAME),
    }
    return rows, fixtures
