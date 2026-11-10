"""Fictional E.164 numbers for seed data.

Every seeded number comes from the UK Ofcom range reserved for drama and fiction,
020 7946 0000 to 020 7946 0999, so none of them can reach a real person or business.
Spain has no equivalent reserved range.
"""

FICTIONAL_PREFIX = "+442079460"
FICTIONAL_DIGITS = 3

SITE_PHONE = FICTIONAL_PREFIX + "000"

_MAX_INDEX = (10**FICTIONAL_DIGITS) - 1 - 1  # reserve 000 for the site


def patient_phone(index: int) -> str:
    if index < 0 or index > _MAX_INDEX:
        msg = f"patient phone index out of range: {index}"
        raise ValueError(msg)
    suffix = f"{index + 1:0{FICTIONAL_DIGITS}d}"
    return FICTIONAL_PREFIX + suffix


def is_fictional(phone: str) -> bool:
    if not phone.startswith(FICTIONAL_PREFIX):
        return False
    rest = phone[len(FICTIONAL_PREFIX) :]
    return len(rest) == FICTIONAL_DIGITS and rest.isdigit()
