"""Presentation labels keep course lifecycle separate from registration state."""


def lifecycle_status(session):
    if session.status in {"registration_open", "registration_closed"}:
        return "confirmed"
    return session.status


def lifecycle_label(session):
    return {
        "draft": "Draft", "confirmed": "Confirmed", "in_progress": "In progress",
        "completed": "Completed", "cancelled": "Cancelled",
    }.get(lifecycle_status(session), session.get_status_display())
