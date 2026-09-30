from dataclasses import dataclass
from typing import Any

# Domain rules: values that change what the checks report and so what a grade means.
# They live in code, not settings, so changes are reviewed and tested.
# Scoring thresholds and weights are in scoring.py.


@dataclass(frozen=True)
class Range:
    """An integer tool parameter: its default and the limits the model may choose within."""

    default: int
    minimum: int
    maximum: int

    def clamp(self, value: int) -> int:
        return max(self.minimum, min(int(value), self.maximum))

    def schema(self, description: str) -> dict[str, Any]:
        """JSON schema for the tool parameter, so the limits the model sees match what clamp() enforces."""
        return {
            "type": "integer",
            "minimum": self.minimum,
            "maximum": self.maximum,
            "description": f"{description} Default {self.default}.",
        }


# HPD violations
VIOLATION_MONTHS = Range(default=36, minimum=1, maximum=120)  # counts cover these months, plus anything still open
VIOLATION_SAMPLE_ROWS = 5000  # categories and examples come from the most recent violations, up to this many

# 311 complaints
COMPLAINT_MONTHS = Range(default=24, minimum=1, maximum=60)
NEIGHBOR_RADIUS_M = Range(default=150, minimum=50, maximum=500)  # buildings within this radius are the comparison

# NYC's heating season runs October 1 to May 31. Heat complaints outside it are ignored.
HEAT_SEASONS = 3  # how many recent heating seasons to break down
HEATING_SEASON_FIRST_MONTH = 10
HEATING_SEASON_LAST_MONTH = 5

# Tenant history
HISTORY_YEARS = Range(default=5, minimum=1, maximum=20)  # evictions and court cases
BEDBUG_PERIODS = 3  # how many annual bedbug reports to check
BEDBUG_PERIOD_FIRST_MONTH = 11  # reporting periods run November 1 to October 31; reports are due December 31

# Landlord portfolios
PORTFOLIO_MAX_REGISTRATIONS = 3000  # larger portfolios are capped, and totals become a lower bound
