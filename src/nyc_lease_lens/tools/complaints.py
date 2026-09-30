import logging
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

import requests

from nyc_lease_lens.context import ContextThreadPoolExecutor
from nyc_lease_lens.tools.base import Tool, ToolError
from nyc_lease_lens.tools.building import PLUTO, hpd_units_on_lots, lot_aliases

logger = logging.getLogger(__name__)

SERVICE_REQUESTS = "erm2-nwe9"
CATEGORIES = ["heat_hot_water", "pests", "mold", "leaks_plumbing", "noise", "sanitation", "repairs"]
REPAIR_TYPES = {
    "PAINT/PLASTER",
    "DOOR/WINDOW",
    "FLOORING/STAIRS",
    "APPLIANCE",
    "ELECTRIC",
    "GENERAL",
    "SAFETY",
    "OUTSIDE BUILDING",
    "ELEVATOR",
    "GENERAL CONSTRUCTION/PLUMBING",
}
HEAT_SEASONS = 3


class Get311Complaints(Tool):
    """Summarize 311 complaints at a building and compare it with nearby buildings."""

    name = "get_311_complaints"
    description = (
        "Summarize 311 complaints about a building (heat/hot water, pests, mold, leaks, noise, "
        "sanitation, repairs) and compare it with nearby buildings per apartment, as a "
        "percentile: 90 means more complaints per unit than 90% of nearby buildings. Also "
        "breaks heat complaints down by heating season (Oct-May), counting the days with "
        "complaints, since one outage can trigger many calls. Use bbl, latitude and "
        "longitude from lookup_building."
    )
    parameters = {
        "type": "object",
        "properties": {
            "bbl": {"type": "string", "description": "10-digit BBL from lookup_building"},
            "latitude": {"type": "number", "description": "Building latitude from lookup_building"},
            "longitude": {"type": "number", "description": "Building longitude from lookup_building"},
            "categories": {
                "type": "array",
                "items": {"type": "string", "enum": CATEGORIES},
                "description": "Only report these categories. Omit for all.",
            },
            "radius_m": {
                "type": "integer",
                "minimum": 50,
                "maximum": 500,
                "description": "Radius in meters for the nearby-building comparison. Default 150.",
            },
            "months": {
                "type": "integer",
                "minimum": 1,
                "maximum": 60,
                "description": "How far back to count complaints. Default 24.",
            },
        },
        "required": ["bbl"],
    }

    def run(
        self,
        bbl: str,
        latitude: float | None = None,
        longitude: float | None = None,
        categories: list[str] | None = None,
        radius_m: int = 150,
        months: int = 24,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"\d{10}", bbl):
            raise ToolError(f"'{bbl}' is not a 10-digit BBL. Call lookup_building first.")
        wanted = [c for c in categories or CATEGORIES if c in CATEGORIES] or CATEGORIES
        radius_m = max(50, min(int(radius_m), 500))
        months = max(1, min(int(months), 60))
        since = (date.today() - timedelta(days=round(months * 30.44))).isoformat()
        compare = latitude is not None and longitude is not None

        # 311 keeps complaints under a lot's old BBL after it is renumbered.
        aliases = self._call(lot_aliases, self.client, bbl)
        ids = ",".join(f"'{b}'" for b in aliases)
        with ContextThreadPoolExecutor() as pool:
            building_future = pool.submit(self._counts_by_bbl, f"bbl in ({ids})", since)
            heat_future = pool.submit(self._heat_days, ids)
            nearby_future = None
            if latitude is not None and longitude is not None:
                nearby_future = pool.submit(
                    self._counts_by_bbl,
                    f"within_circle(location, {float(latitude)}, {float(longitude)}, {radius_m}) "
                    f"AND bbl not in ({ids}) AND bbl IS NOT NULL",
                    since,
                )
            building, heat = building_future.result(), heat_future.result()
            nearby = nearby_future.result() if nearby_future else {}

        counts: Counter[str] = sum(building.values(), Counter())
        result: dict[str, Any] = {"bbl": bbl, "counting_since": since}
        notes: list[str] = []
        if len(aliases) > 1:
            old = ", ".join(a for a in aliases if a != bbl)
            logger.info("lot renumbered: including old BBLs", extra={"bbl": bbl, "old_bbls": old})
            notes.append(f"This lot was renumbered; complaints filed under {old} are included.")

        if nearby:
            units = self._residential_units([*aliases, *nearby])
            own_units = max(units.get(a, 0) for a in aliases) or self._call(hpd_units_on_lots, self.client, aliases)
            result["compared_with"] = (
                f"{sum(1 for b in nearby if units.get(b))} residential buildings within {radius_m} m "
                "that had any 311 complaints"
            )
            result["residential_units"] = own_units or None
            result["categories"] = _compare(counts, own_units, nearby, units, wanted)
            if not own_units:
                logger.info("unit count unknown: no per-unit comparison", extra={"bbl": bbl})
                notes.append("Unit count unknown, so per-unit comparison is unavailable for this building.")
        else:
            result["categories"] = [{"category": c, "complaints": counts[c]} for c in wanted if counts[c]]
            if not compare:
                notes.append("Pass latitude and longitude to compare with nearby buildings.")

        if "heat_hot_water" in wanted:
            result["heat_seasons"] = _heat_seasons(heat)
        if other := counts["other"]:
            result["other_complaints"] = other
        if not counts.total():
            notes.append("No 311 complaints about this building in this period.")
        if notes:
            result["notes"] = notes
        logger.debug(
            "complaints summarized",
            extra={"bbl": bbl, "complaints": counts.total(), "nearby_buildings": len(nearby)},
        )
        return result

    def _counts_by_bbl(self, where: str, since: str) -> dict[str, Counter]:
        rows = self._query(
            SERVICE_REQUESTS,
            {
                "$select": "bbl, complaint_type, descriptor, count(*) AS n",
                "$where": f"{where} AND created_date >= '{since}'",
                "$group": "bbl, complaint_type, descriptor",
                "$limit": 50000,
            },
        )
        counts: dict[str, Counter] = defaultdict(Counter)
        for row in rows:
            counts[row["bbl"]][_categorize(row["complaint_type"], row.get("descriptor"))] += int(row["n"])
        return counts

    def _heat_days(self, ids: str) -> list[dict]:
        return self._query(
            SERVICE_REQUESTS,
            {
                "$select": "date_trunc_ymd(created_date) AS day, count(*) AS n",
                "$where": f"bbl in ({ids}) AND complaint_type='HEAT/HOT WATER' "
                f"AND created_date >= '{_season_start(HEAT_SEASONS - 1).isoformat()}'",
                "$group": "day",
                "$limit": 5000,
            },
        )

    def _residential_units(self, bbls: list[str]) -> dict[str, int]:
        ids = ",".join(bbls)  # PLUTO stores bbl as a number
        rows = self._query(PLUTO, {"$select": "bbl, unitsres", "$where": f"bbl in ({ids})", "$limit": len(bbls)})
        return {str(int(float(r["bbl"]))): int(float(r.get("unitsres") or 0)) for r in rows}

    def _query(self, dataset: str, params: dict) -> list[dict]:
        return self._call(self.client.socrata, dataset, params)

    @staticmethod
    def _call(fn, *args):
        try:
            return fn(*args)
        except requests.RequestException as e:
            raise ToolError(f"311 complaints lookup failed: {e}") from e


def _categorize(complaint_type: str, descriptor: str | None) -> str:
    kind, detail = complaint_type.upper(), (descriptor or "").upper()
    if kind in ("HEAT/HOT WATER", "BOILERS"):
        return "heat_hot_water"
    if kind == "RODENT" or (kind == "UNSANITARY CONDITION" and "PEST" in detail):
        return "pests"
    if "MOLD" in detail:
        return "mold"
    if kind in ("PLUMBING", "WATER LEAK") or "SEWAGE" in detail:
        return "leaks_plumbing"
    if kind.startswith("NOISE"):
        return "noise"
    if kind in ("UNSANITARY CONDITION", "DIRTY CONDITION"):
        return "sanitation"
    if kind in REPAIR_TYPES:
        return "repairs"
    return "other"


def _compare(counts: Counter, units: int | None, nearby: dict, all_units: dict, wanted: list[str]) -> list[dict]:
    peers = {b: c for b, c in nearby.items() if all_units.get(b)}
    rows = []
    for category in wanted:
        row = {"category": category, "complaints": counts[category]}
        if units and peers:
            rate = counts[category] / units
            peer_rates = [c[category] / all_units[b] for b, c in peers.items()]
            row["per_100_units"] = round(100 * rate, 1)
            row["nearby_median_per_100_units"] = round(100 * median(peer_rates), 1)
            row["percentile_vs_nearby"] = round(100 * sum(r < rate for r in peer_rates) / len(peer_rates))
        if row["complaints"] or row.get("nearby_median_per_100_units"):
            rows.append(row)
    return sorted(rows, key=lambda r: (r.get("percentile_vs_nearby", 0), r["complaints"]), reverse=True)


def _season_start(seasons_ago: int = 0) -> date:
    today = date.today()
    start_year = today.year if today.month >= 10 else today.year - 1
    return date(start_year - seasons_ago, 10, 1)


def _heat_seasons(days: list[dict]) -> list[dict]:
    seasons: dict[int, dict[str, Any]] = {}
    for i in reversed(range(HEAT_SEASONS)):
        start = _season_start(i)
        seasons[start.year] = {
            "season": f"{start.year}-{(start.year + 1) % 100:02d}",
            "complaints": 0,
            "days_with_complaints": 0,
        }
    for row in days:
        day = date.fromisoformat(row["day"][:10])
        if 6 <= day.month <= 9:
            continue
        season = seasons.get(day.year if day.month >= 10 else day.year - 1)
        if season:
            season["complaints"] += int(row["n"])
            season["days_with_complaints"] += 1
    return list(seasons.values())
