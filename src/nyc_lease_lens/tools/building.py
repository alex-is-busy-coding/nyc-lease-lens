import logging
import re
from typing import Any

import requests

from nyc_lease_lens import datasets
from nyc_lease_lens.opendata import OpenDataClient
from nyc_lease_lens.parsing import to_int
from nyc_lease_lens.tools.base import Tool, ToolError

logger = logging.getLogger(__name__)

BOROUGHS = ["Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"]


class LookupBuilding(Tool):
    """Resolve an NYC address to its BBL/BIN and basic building facts."""

    name = "lookup_building"
    error_label = "Address lookup"
    data_sources = (datasets.GEOSEARCH, datasets.PLUTO, datasets.HPD_BUILDINGS)
    description = (
        "Identify an NYC building from a street address. Returns its BBL (tax lot ID) and BIN "
        "(building ID), coordinates, and facts such as year built and number of apartments. "
        "Call this first for any address. If the result is ambiguous, ask the user which borough."
    )
    parameters = {
        "type": "object",
        "properties": {
            "address": {"type": "string", "description": "Street address, e.g. '157 Ludlow St' or '89-11 Queens Blvd'"},
            "borough": {
                "type": "string",
                "enum": BOROUGHS,
                "description": "Borough, if the user mentioned it or it is clear from context.",
            },
        },
        "required": ["address"],
    }

    def run(self, address: str, borough: str | None = None) -> dict[str, Any]:
        candidates = self._find_candidates(address, borough)
        if not candidates:
            logger.info("address not found", extra={"borough": borough})
            raise ToolError(f"No NYC building found for '{address}'. Check the house number and street.")

        # GeoSearch ranks on text alone, so the same street address in two boroughs ties.
        same_address = {c["bbl"]: c for c in candidates if c["name"] == candidates[0]["name"]}
        if len(same_address) > 1:
            logger.info("address ambiguous", extra={"candidates": len(same_address)})
            return {
                "ambiguous": True,
                "message": "This address exists in more than one borough. Ask the user which one they mean.",
                "candidates": [{k: c[k] for k in ("address", "borough", "zip")} for c in same_address.values()],
            }

        place = candidates[0]
        del place["name"]
        building, notes = self._building_facts(place["bbl"], place["bin"])
        if building:
            place["building"] = building
        if notes:
            place["notes"] = notes
        logger.info(
            "building resolved",
            extra={
                "bbl": place["bbl"],
                "bin": place["bin"],
                "apartments": (building or {}).get("apartments_in_building"),
            },
        )
        return place

    def _find_candidates(self, address: str, borough: str | None) -> list[dict]:
        features = self.fetch(self.client.geosearch, address)

        zip_code = re.search(r"\b1\d{4}\b", address)
        return [
            c
            for c in map(self._candidate, features)
            if c
            and (not borough or c["borough"].lower() == borough.lower())
            and (not zip_code or c["zip"] == zip_code.group())
        ]

    def _building_facts(self, bbl: str, bin: str | None) -> tuple[dict | None, list[str]]:
        notes = []
        try:
            rows = self.client.socrata(datasets.PLUTO, {"$where": f"bbl={bbl}"})
        except requests.RequestException as e:
            rows = []
            notes.append(f"PLUTO lookup failed ({e}).")
        try:
            apartments = hpd_apartments(self.client, bin) if bin else None
        except requests.RequestException as e:
            logger.warning("HPD apartment count unavailable", extra={"bin": bin, "error": str(e)[:200]})
            apartments = None

        building = {}
        if rows:
            row = rows[0]
            building = {
                "year_built": to_int(row.get("yearbuilt")) or None,  # PLUTO uses 0 for unknown
                "floors": to_int(row.get("numfloors")),
                "residential_units_on_lot": to_int(row.get("unitsres")),
                "total_units_on_lot": to_int(row.get("unitstotal")),
                "building_class": row.get("bldgclass"),
                "owner_of_record": row.get("ownername"),
            }
        elif not notes:
            notes.append("No PLUTO record for this lot, so tax-lot facts are unavailable.")
        if apartments:
            building["apartments_in_building"] = apartments

        if not apartments and building.get("residential_units_on_lot") == 0:
            notes.append("No residential units on record: this may not be an apartment building.")
        elif apartments and rows and not building.get("residential_units_on_lot"):
            logger.info("PLUTO record looks outdated", extra={"bbl": bbl, "bin": bin})
            notes.append("PLUTO's record for this lot looks outdated (it may have been renumbered).")
        return building or None, notes

    @staticmethod
    def _candidate(feature: dict) -> dict | None:
        props = feature["properties"]
        pad = (props.get("addendum") or {}).get("pad") or {}
        if not pad.get("bbl", "").isdigit():
            return None
        lon, lat = feature["geometry"]["coordinates"]
        return {
            "name": props["name"],
            "address": props["label"].removesuffix(", USA"),
            "borough": props.get("borough", ""),
            "zip": props.get("postalcode", ""),
            "bbl": pad["bbl"],
            "bin": pad.get("bin"),
            "latitude": lat,
            "longitude": lon,
        }


def hpd_apartments(client: OpenDataClient, bin: str) -> int | None:
    """Legal apartment count for one building from HPD's register. BINs survive lot renumbering."""
    rows = client.socrata(
        datasets.HPD_BUILDINGS, {"$select": "legalclassa", "$where": f"bin='{bin}' AND recordstatus='Active'"}
    )
    return sum(to_int(r.get("legalclassa")) or 0 for r in rows) or None


def lot_aliases(client: OpenDataClient, bbl: str) -> list[str]:
    """The BBL plus any older numbers for the same lot. HPD keeps the old block/lot on violations."""
    rows = client.socrata(
        datasets.HPD_VIOLATIONS,
        {"$select": "boroid, block, lot", "$where": f"bbl='{bbl}'", "$group": "boroid, block, lot"},
    )
    aliases = {bbl}
    for r in rows:
        if r.get("boroid") and r.get("block") and r.get("lot"):
            aliases.add(f"{int(r['boroid'])}{int(r['block']):05d}{int(r['lot']):04d}")
    return sorted(aliases)


def hpd_units_on_lots(client: OpenDataClient, bbls: list[str]) -> int:
    lots = " OR ".join(f"(boroid='{b[0]}' AND block='{int(b[1:6])}' AND lot='{int(b[6:])}')" for b in bbls)
    rows = client.socrata(
        datasets.HPD_BUILDINGS,
        {"$select": "legalclassa", "$where": f"({lots}) AND recordstatus='Active'", "$limit": 5000},
    )
    return sum(to_int(r.get("legalclassa")) or 0 for r in rows)
