from datetime import date
from typing import Any

from nyc_lease_lens.scoring.engine import (
    GRADES,
    RECENT_YEARS,
    SCALE,
    Facts,
    GoodSign,
    Hit,
    Rule,
    describe_points,
    grade,
    grade_bands,
    units_for,
)
from nyc_lease_lens.scoring.red_flags import GOOD_SIGNS, RULES

__all__ = [
    "GOOD_SIGNS",
    "GRADES",
    "RECENT_YEARS",
    "RULES",
    "SCALE",
    "Facts",
    "GoodSign",
    "Hit",
    "Rule",
    "describe_points",
    "grade_bands",
    "score",
]


def score(
    building: dict[str, Any],
    violations: dict[str, Any] | None,
    complaints: dict[str, Any] | None,
    landlord: dict[str, Any] | None,
    history: dict[str, Any] | None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Grade a building from the check results. No network calls, so the rules are easy to test."""
    facts = Facts(
        building=building,
        violations=violations or {},
        complaints=complaints or {},
        landlord=landlord or {},
        history=history or {},
        units=units_for(building, complaints),
        today=today or date.today(),
    )
    return grade(facts, RULES, GOOD_SIGNS)
