import re
from datetime import datetime

# Socrata returns every value as a string, and some fields are missing or malformed.


def to_int(value: str | None) -> int | None:
    """'12', '12.0' and '1008350041.00000000' become ints; missing or malformed values become None."""
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


def to_date(value: str | None) -> str | None:
    """An ISO date (YYYY-MM-DD) from a Socrata timestamp, or from litigation's MM/DD/YYYY finding dates."""
    if not value:
        return None
    if re.match(r"\d{2}/\d{2}/\d{4}", value):
        return datetime.strptime(value[:10], "%m/%d/%Y").date().isoformat()
    return value[:10]
