from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from nyc_lease_lens import scoring
from nyc_lease_lens.opendata import OpenDataClient
from nyc_lease_lens.tools.base import Tool, ToolError
from nyc_lease_lens.tools.building import BOROUGHS, LookupBuilding
from nyc_lease_lens.tools.complaints import Get311Complaints
from nyc_lease_lens.tools.history import GetTenantHistory
from nyc_lease_lens.tools.landlord import GetLandlordProfile
from nyc_lease_lens.tools.violations import GetHpdViolations

# Builds a check's arguments from the lookup result, or None when it can't run.
ArgsFrom = Callable[[dict[str, Any]], dict[str, Any] | None]


class ScoreBuildingRisk(Tool):
    """Run every check on a building in parallel and grade it."""

    name = "score_building_risk"
    description = (
        "Check an NYC building end to end and grade it from A (no red flags) to F. Runs the "
        "violations, 311 complaints, landlord and tenant history checks in parallel and returns "
        "the grade, a 0-100 score, the red flags with their points and sources, good signs, and "
        "any checks that failed. Call this first when the user gives an address; use the "
        "individual tools only for follow-up detail."
    )
    parameters = {
        "type": "object",
        "properties": {
            "address": {"type": "string", "description": "Street address, e.g. '157 Ludlow St'"},
            "borough": {
                "type": "string",
                "enum": BOROUGHS,
                "description": "Borough, if the user mentioned it or it is clear from context.",
            },
        },
        "required": ["address"],
    }

    lookup_tool: type[Tool] = LookupBuilding
    # Run in parallel after the lookup. Keys are the matching scoring.score() parameters.
    # The README flow diagram is generated from this table.
    checks: dict[str, tuple[type[Tool], ArgsFrom]] = {
        "violations": (GetHpdViolations, lambda b: {"bbl": b["bbl"]}),
        "complaints": (
            Get311Complaints,
            lambda b: {"bbl": b["bbl"], "latitude": b["latitude"], "longitude": b["longitude"]},
        ),
        "landlord": (GetLandlordProfile, lambda b: {"bin": b["bin"]} if b.get("bin") else None),
        "history": (GetTenantHistory, lambda b: {"bbl": b["bbl"]}),
    }

    def __init__(self, client: OpenDataClient):
        super().__init__(client)
        self.lookup = self.lookup_tool(client)
        self.check_tools = {name: tool(client) for name, (tool, _) in self.checks.items()}

    def run(self, address: str, borough: str | None = None) -> dict[str, Any]:
        building = self.lookup.run(address=address, borough=borough)
        if building.get("ambiguous"):
            return building
        facts = building.get("building") or {}
        if not facts.get("apartments_in_building") and facts.get("residential_units_on_lot") == 0:
            return {
                "address": building["address"],
                "bbl": building["bbl"],
                "grade": None,
                "notes": ["No apartments on record for this building, so there is no rental risk grade."],
            }

        results: dict[str, dict[str, Any] | None] = dict.fromkeys(self.checks)
        gaps: list[str] = []
        with ThreadPoolExecutor() as pool:
            futures = {}
            for name, (_, args_from) in self.checks.items():
                if (args := args_from(building)) is None:
                    gaps.append(f"{name} check skipped: not enough building data")
                else:
                    futures[name] = pool.submit(self.check_tools[name].run, **args)
            for name, future in futures.items():
                try:
                    results[name] = future.result()
                except ToolError as e:
                    gaps.append(f"{name} check failed: {e}")

        report = {
            "address": building["address"],
            "bbl": building["bbl"],
            "bin": building.get("bin"),
            **scoring.score(building, **results),
        }
        notes = building.get("notes", [])
        if gaps:
            report["data_gaps"] = gaps
            notes = [*notes, "Some checks failed, so the grade may understate the risk."]
        if notes:
            report["notes"] = notes
        return report
