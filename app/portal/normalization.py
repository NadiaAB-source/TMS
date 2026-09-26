"""Small, centralised normalisers used at every write boundary."""


def normalize_email(value):
    """Return the canonical email value required by IQARUS.

    The TMS treats email addresses as case-insensitive identifiers.  Keeping
    this in one function prevents new forms and imports from drifting apart.
    """

    return str(value or "").strip().lower()
