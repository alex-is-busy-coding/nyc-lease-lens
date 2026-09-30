import logging
import re
from collections import Counter
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

import requests

from nyc_lease_lens import datasets
from nyc_lease_lens.rules import VIOLATION_MONTHS, VIOLATION_SAMPLE_ROWS
from nyc_lease_lens.tools.base import Tool, ToolError

logger = logging.getLogger(__name__)

CLASSES = ["C", "B", "A", "I"]

CATEGORIES = [
    ("lead_paint", r"\bLEAD\b"),
    ("mold", r"\bMOLD"),
    ("pests", r"MICE|\bRATS?\b|ROACH|VERMIN|BED ?BUG|\bPESTS?\b|WATER BUGS|INFESTATION"),
    ("heat_hot_water", r"\bHEAT|HOT WATER|BOILER"),
    ("smoke_co_detectors", r"SMOKE|CARBON MONOXIDE"),
    ("fire_safety", r"SELF-CLOSING|EGRESS|FIRE|WINDOW GUARD"),
    ("gas_electric", r"\bGAS\b|ELECTRIC"),
    ("illegal_occupancy", r"DISCONTINUE USE|UNLAWFUL|CERTIFICATE OF OCCUPANCY|ILLEGAL"),
    ("leaks_plumbing", r"LEAK|PLUMBING|WATER DAMAGE|SEWAGE"),
    ("garbage", r"REFUSE|RUBBISH|GARBAGE"),
]


class GetHpdViolations(Tool):
    """Summarize a building's HPD housing code violations by class, status and type."""

    name = "get_hpd_violations"
    data_sources = (datasets.HPD_VIOLATIONS,)
    description = (
        "Summarize HPD housing code violations for a building, by severity class and type "
        "(pests, mold, heat/hot water, lead paint, fire safety, illegal occupancy, ...). "
        "Classes: C = immediately hazardous, B = hazardous, A = non-hazardous, "
        "I = information order. Rent-impairing violations are serious enough that tenants "
        "may withhold rent. Covers every violation that is still open, of any age, plus all "
        "issued in the last `months`. Use the bbl from lookup_building."
    )
    parameters = {
        "type": "object",
        "properties": {
            "bbl": {"type": "string", "description": "10-digit BBL from lookup_building"},
            "months": VIOLATION_MONTHS.schema("How far back to count violations, open or closed."),
        },
        "required": ["bbl"],
    }

    def run(self, bbl: str, months: int = VIOLATION_MONTHS.default) -> dict[str, Any]:
        if not re.fullmatch(r"\d{10}", bbl):
            raise ToolError(f"'{bbl}' is not a 10-digit BBL. Call lookup_building first.")
        months = VIOLATION_MONTHS.clamp(months)
        since = date.today() - timedelta(days=round(months * 30.44))

        where = f"bbl='{bbl}' AND (inspectiondate >= '{since.isoformat()}' OR violationstatus='Open')"
        groups = self._query(
            {
                "$select": f"class, violationstatus, rentimpairing, "
                f"case(inspectiondate >= '{since.isoformat()}', 'recent', true, 'older') AS period, "
                f"count(*) AS n",
                "$where": where,
                "$group": "class, violationstatus, rentimpairing, period",
            }
        )
        result: dict[str, Any] = {"bbl": bbl, "counting_since": since.isoformat()}
        if not groups:
            result["notes"] = ["No open HPD violations, and none issued in this period."]
            return result

        notes: list[str] = []
        rows = self._optional_query(
            notes,
            "categories and examples",
            {
                "$select": "class, violationstatus, inspectiondate, novdescription, apartment, housenumber, streetname",
                "$where": where,
                "$order": "inspectiondate DESC",
                "$limit": VIOLATION_SAMPLE_ROWS,
            },
        )
        oldest = self._optional_query(
            notes,
            "the oldest open violation",
            {
                "$select": "class, violationstatus, inspectiondate, novdescription, apartment",
                "$where": f"bbl='{bbl}' AND violationstatus='Open' AND class in ('B', 'C') "
                "AND inspectiondate IS NOT NULL",
                "$order": "inspectiondate ASC",
                "$limit": 1,
            },
        )
        for row in rows + oldest:
            row["inspectiondate"] = row.get("inspectiondate", "")
            row["_open"] = row.get("violationstatus") == "Open"
            row["_recent"] = row["inspectiondate"][:10] >= since.isoformat()
            row["_category"] = _categorize(row.get("novdescription", ""))

        open_rows = [r for r in rows if r["_open"]]
        result |= {
            "open_now": _sum_by_class(g for g in groups if g["violationstatus"] == "Open"),
            f"issued_last_{months}_months": _sum_by_class(g for g in groups if g["period"] == "recent"),
            "rent_impairing_open": sum(
                int(g["n"]) for g in groups if g["violationstatus"] == "Open" and g.get("rentimpairing") == "Y"
            ),
            "categories": _categories(open_rows, [r for r in rows if r["_recent"]]),
            "oldest_open_hazardous": _oldest_hazard(oldest[0]) if oldest else None,
            "latest_open_class_c": [_example(r) for r in open_rows if r.get("class") == "C"][:5],
        }

        addresses = sorted({f"{r.get('housenumber', '')} {r.get('streetname', '')}".strip() for r in rows})
        if len(addresses) > 1:
            result["addresses_on_lot"] = addresses[:10]
            notes.append(f"This tax lot has {len(addresses)} addresses; counts cover all of them.")
        if len(rows) >= VIOLATION_SAMPLE_ROWS:
            notes.append(
                f"Class counts are exact; categories are based on the {VIOLATION_SAMPLE_ROWS} "
                "most recent violations only."
            )
        if notes:
            result["notes"] = notes
        logger.debug(
            "violations summarized",
            extra={"bbl": bbl, "open": result["open_now"].get("total"), "open_c": result["open_now"].get("C", 0)},
        )
        return result

    def _query(self, params: dict) -> list[dict]:
        try:
            return self.client.socrata(datasets.HPD_VIOLATIONS.id, params)
        except requests.RequestException as e:
            raise ToolError(f"HPD violations lookup failed: {e}") from e

    def _optional_query(self, notes: list[str], what: str, params: dict) -> list[dict]:
        try:
            return self._query(params)
        except ToolError as e:
            logger.warning("optional violations query failed", extra={"query": what, "error": str(e)[:200]})
            notes.append(f"Could not load {what} (NYC Open Data was slow); the class counts are complete.")
            return []


def _categorize(description: str) -> str:
    text = description.upper()
    for category, pattern in CATEGORIES:
        if re.search(pattern, text):
            return category
    return "other_repairs"


def _sum_by_class(groups: Iterable[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for g in groups:
        counts[g.get("class", "")] += int(g["n"])
    return {"total": counts.total()} | {c: counts[c] for c in CLASSES if counts[c]}


def _categories(open_rows: list[dict], recent_rows: list[dict]) -> list[dict]:
    open_counts = Counter(r["_category"] for r in open_rows)
    recent_counts = Counter(r["_category"] for r in recent_rows)
    open_c = Counter(r["_category"] for r in open_rows if r.get("class") == "C")
    return sorted(
        (
            {"category": cat, "open": open_counts[cat], "open_class_c": open_c[cat], "recent": recent_counts[cat]}
            for cat in open_counts | recent_counts
        ),
        key=lambda c: (c["open_class_c"], c["open"], c["recent"]),
        reverse=True,
    )


def _oldest_hazard(row: dict) -> dict:
    days_open = (date.today() - date.fromisoformat(row["inspectiondate"][:10])).days
    return _example(row) | {"class": row["class"], "days_open": days_open}


def _example(row: dict) -> dict:
    example = {
        "inspected": row["inspectiondate"][:10] or None,
        "category": row["_category"],
        "description": _clean(row.get("novdescription", "")),
    }
    if row.get("apartment"):
        example["apartment"] = row["apartment"]
    return example


def _clean(description: str, limit: int = 180) -> str:
    """Drop the leading legal citation (§ 27-2045(B)(1) HMC, RCNY ...) and shorten."""
    words = description.split()
    start = next((i for i, w in enumerate(words) if not _is_citation(w)), len(words))
    text = (" ".join(words[start:]) or description).capitalize()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


CITATION_WORDS = {"SECTION", "HMC", "ADM", "CODE", "RCNY", "MDL", "M/D", "LAW", "NYC", "AND", "&"}


def _is_citation(word: str) -> bool:
    core = word.strip(",;:.-")
    return (
        core.upper() in CITATION_WORDS
        or not re.search(r"[A-Za-z]", core)
        or re.fullmatch(r"[\d§.\-]*(\([A-Za-z0-9]+\))+", core) is not None
    )
