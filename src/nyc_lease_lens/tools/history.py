import logging
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from nyc_lease_lens.data import datasets
from nyc_lease_lens.data.parsing import to_date, to_int
from nyc_lease_lens.data.soql import in_list
from nyc_lease_lens.observability.context import ContextThreadPoolExecutor
from nyc_lease_lens.rules import BEDBUG_PERIOD_FIRST_MONTH, BEDBUG_PERIODS, HISTORY_YEARS
from nyc_lease_lens.tools.base import Tool
from nyc_lease_lens.tools.building import lot_aliases

logger = logging.getLogger(__name__)


HARASSMENT_FOUND = ("After Trial", "After Inquest")
CASE_KINDS = {
    "Tenant Action": "tenant_repair_cases",
    "Tenant Action/Harrassment": "harassment_cases",
    "Heat and Hot Water": "heat_hot_water_cases",
    "Heat Supplemental Cases": "heat_hot_water_cases",
    "Comprehensive": "city_comprehensive_cases",
    "Comp Supplemental Cases": "city_comprehensive_cases",
    "False Certification Non-Lead": "false_repair_certifications",
    "Lead False Certification": "false_repair_certifications",
    "7A": "court_appointed_administrator",
    "Failure to Register Only": "failure_to_register",
    "Access Warrant - Non-Lead": "access_warrants",
    "Access Warrant - lead": "access_warrants",
}


class GetTenantHistory(Tool):
    """Evictions, bedbug reports, housing court cases and vacate orders for a building."""

    name = "get_tenant_history"
    error_label = "Tenant history lookup"
    data_sources = (
        datasets.EVICTIONS,
        datasets.BEDBUGS,
        datasets.LITIGATIONS,
        datasets.VACATE_ORDERS,
        datasets.HPD_VIOLATIONS,
    )
    description = (
        "Summarize what has happened to tenants in a building: executed residential evictions by "
        "year, the owner's annual bedbug reports, HPD housing court cases (tenant repair actions, "
        "harassment, heat, false repair certifications, court-appointed 7A administrators), "
        "harassment findings, and vacate orders that forced tenants out. Use the bbl from "
        "lookup_building."
    )
    parameters = {
        "type": "object",
        "properties": {
            "bbl": {"type": "string", "description": "10-digit BBL from lookup_building"},
            "years": HISTORY_YEARS.schema(
                "How far back to count evictions and court cases. "
                "Harassment findings and 7A administrators are reported from any year."
            ),
        },
        "required": ["bbl"],
    }

    def run(self, bbl: str, years: int = HISTORY_YEARS.default) -> dict[str, Any]:
        bbl = self.validate_bbl(bbl)
        years = HISTORY_YEARS.clamp(years)
        today = date.today()
        since = date(today.year - years, today.month, min(today.day, 28)).isoformat()

        aliases = self.fetch(lot_aliases, self.client, bbl)
        lots = f"bbl in {in_list(aliases)}"

        with ContextThreadPoolExecutor() as pool:
            evictions = pool.submit(self._evictions, lots, since)
            bedbugs = pool.submit(self._bedbugs, lots)
            court = pool.submit(self._court, lots, since)
            vacates = pool.submit(self._vacates, lots, since)
            result: dict[str, Any] = {
                "bbl": bbl,
                "counting_since": since,
                "evictions": evictions.result(),
                "bedbug_reports": bedbugs.result(),
                "housing_court": court.result(),
                "vacate_orders": vacates.result(),
            }

        notes = []
        if len(aliases) > 1:
            old = ", ".join(a for a in aliases if a != bbl)
            notes.append(f"This lot was renumbered; records under {old} are included.")
        if not result["bedbug_reports"]:
            notes.append(
                "No bedbug reports on file for this lot. Buildings with 3 or more apartments must file one every year."
            )
        elif missing := [p["period_start"] for p in result["bedbug_reports"] if not p["filed"]]:
            notes.append(
                f"No bedbug report filed for the period(s) starting {', '.join(missing)}. Owners of buildings "
                "with 3 or more apartments must file one every year."
            )
        if notes:
            result["notes"] = notes
        logger.debug(
            "tenant history summarized",
            extra={
                "bbl": bbl,
                "evictions": result["evictions"]["total"],
                "vacates_in_effect": len(result["vacate_orders"]["in_effect"]),
                "harassment_findings": len(result["housing_court"]["harassment_findings"]),
            },
        )
        return result

    def _evictions(self, lots: str, since: str) -> dict[str, Any]:
        rows = self.query(
            datasets.EVICTIONS,
            {
                "$select": "date_extract_y(executed_date) AS year, count(*) AS n",
                "$where": f"{lots} AND residential_commercial_ind='Residential' AND executed_date >= '{since}'",
                "$group": "year",
                "$order": "year DESC",
            },
        )
        by_year = {r["year"]: int(r["n"]) for r in rows}
        return {"total": sum(by_year.values()), "by_year": by_year}

    def _bedbugs(self, lots: str) -> list[dict[str, Any]]:
        rows = self.query(
            datasets.BEDBUGS,
            {
                "$select": "building_id, filing_date, filing_period_start_date, filling_period_end_date, "
                "of_dwelling_units, infested_dwelling_unit_count, eradicated_unit_count, re_infested_dwelling_unit",
                "$where": lots,
                "$order": "filing_date DESC",
                "$limit": 1000,
            },
        )
        # One report per building per period; keep the latest filing for each and add up the lot.
        latest: dict[tuple[str, str], dict] = {}
        for row in rows:
            latest.setdefault((row.get("filing_period_start_date", "")[:10], row.get("building_id", "")), row)
        periods: dict[str, Counter[str]] = defaultdict(Counter)
        for (start, _), row in latest.items():
            period = periods[start]
            period["buildings_reporting"] += 1
            period["apartments"] += to_int(row.get("of_dwelling_units")) or 0
            period["infested"] += to_int(row.get("infested_dwelling_unit_count")) or 0
            period["reinfested"] += to_int(row.get("re_infested_dwelling_unit")) or 0
            period["eradicated"] += to_int(row.get("eradicated_unit_count")) or 0
        if not periods:
            return []
        buildings = len({building for _, building in latest})
        return [
            {"period_start": start, "filed": True, "buildings_on_lot": buildings, **periods[start]}
            if start in periods
            else {"period_start": start, "filed": False}
            for start in _due_bedbug_periods()
        ]

    def _court(self, lots: str, since: str) -> dict[str, Any]:
        rows = self.query(
            datasets.LITIGATIONS,
            {
                "$select": "casetype, casestatus, caseopendate, findingofharassment, findingdate, penalty",
                "$where": f"{lots} AND (caseopendate >= '{since}' OR casetype='7A' "
                f"OR findingofharassment in {in_list(HARASSMENT_FOUND)})",
                "$limit": 5000,
            },
        )
        recent = [r for r in rows if r.get("caseopendate", "") >= since]
        kinds = Counter(CASE_KINDS.get(r.get("casetype", ""), "other_cases") for r in recent)
        court: dict[str, Any] = {
            "cases": dict(kinds.most_common()),
            "open_cases": sum("PENDING" in r.get("casestatus", "").upper() for r in recent),
        }
        findings = [r for r in rows if r.get("findingofharassment") in HARASSMENT_FOUND]
        court["harassment_findings"] = [
            {
                "decided": to_date(r.get("findingdate")) or to_date(r.get("caseopendate")),
                "how": r["findingofharassment"].lower(),
                "penalty": int(float(r["penalty"])) if r.get("penalty") else None,
            }
            for r in findings
        ]
        administrators = [r for r in rows if r.get("casetype") == "7A"]
        if administrators:
            court["court_appointed_administrator"] = {
                "cases": len(administrators),
                "opened": sorted(d for r in administrators if (d := to_date(r.get("caseopendate")))),
            }
        return court

    def _vacates(self, lots: str, since: str) -> dict[str, Any]:
        rows = self.query(
            datasets.VACATE_ORDERS,
            {
                "$select": "house_number, street_name, primary_vacate_reason, vacate_type, "
                "vacate_effective_date, actual_rescind_date, number_of_vacated_units",
                "$where": lots,
                "$order": "vacate_effective_date DESC",
            },
        )
        active, rescinded = [], 0
        for row in rows:
            effective, rescind = to_date(row.get("vacate_effective_date")), to_date(row.get("actual_rescind_date"))
            if not rescind or (effective and rescind < effective):
                active.append(
                    {
                        "since": effective,
                        "reason": row.get("primary_vacate_reason"),
                        "scope": row.get("vacate_type"),
                        "units": to_int(row.get("number_of_vacated_units")) or None,
                        "address": f"{row.get('house_number', '')} {row.get('street_name', '')}".strip().title(),
                    }
                )
            elif effective and effective >= since:
                rescinded += 1
        return {"in_effect": active, "rescinded_since": rescinded}


def _due_bedbug_periods() -> list[str]:
    """Start dates of the latest reporting periods whose deadline has passed, newest first.

    Periods run Nov 1 - Oct 31 and reports are due by Dec 31, so the latest due period
    always started on Nov 1 two calendar years ago.
    """
    latest = date.today().year - 2
    return [date(latest - i, BEDBUG_PERIOD_FIRST_MONTH, 1).isoformat() for i in range(BEDBUG_PERIODS)]
