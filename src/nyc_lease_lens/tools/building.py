import re

import requests

from nyc_lease_lens.opendata import OpenDataClient
from nyc_lease_lens.tools.base import Tool, ToolError

PLUTO = "64uk-42ks"
BOROUGHS = ["Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"]


class LookupBuilding(Tool):
    """Resolve an NYC address to its BBL/BIN and basic building facts."""

    name = "lookup_building"
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

    def __init__(self, client: OpenDataClient):
        self.client = client

    def run(self, address: str, borough: str | None = None) -> dict:
        candidates = self._find_candidates(address, borough)
        if not candidates:
            raise ToolError(f"No NYC building found for '{address}'. Check the house number and street.")

        # GeoSearch ranks on text alone, so the same street address in two boroughs ties.
        same_address = {c["bbl"]: c for c in candidates if c["name"] == candidates[0]["name"]}
        if len(same_address) > 1:
            return {
                "ambiguous": True,
                "message": "This address exists in more than one borough. Ask the user which one they mean.",
                "candidates": [{k: c[k] for k in ("address", "borough", "zip")} for c in same_address.values()],
            }

        place = candidates[0]
        del place["name"]
        building, notes = self._building_facts(place["bbl"])
        if building:
            place["building"] = building
        if notes:
            place["notes"] = notes
        return place

    def _find_candidates(self, address: str, borough: str | None) -> list[dict]:
        try:
            features = self.client.geosearch(address)
        except requests.RequestException as e:
            raise ToolError(f"Address lookup failed: {e}") from e

        zip_code = re.search(r"\b1\d{4}\b", address)
        return [
            c for c in map(self._candidate, features)
            if c
            and (not borough or c["borough"].lower() == borough.lower())
            and (not zip_code or c["zip"] == zip_code.group())
        ]

    def _building_facts(self, bbl: str) -> tuple[dict | None, list[str]]:
        try:
            rows = self.client.socrata(PLUTO, {"$where": f"bbl={bbl}"})
        except requests.RequestException as e:
            return None, [f"Building facts unavailable: PLUTO lookup failed ({e})."]
        if not rows:
            return None, ["No PLUTO record for this lot (common for condos), so building facts are unavailable."]

        row = rows[0]
        building = {
            "year_built": _int(row.get("yearbuilt")) or None,  # PLUTO uses 0 for unknown
            "floors": _int(row.get("numfloors")),
            "residential_units": _int(row.get("unitsres")),
            "total_units": _int(row.get("unitstotal")),
            "building_class": row.get("bldgclass"),
            "owner_of_record": row.get("ownername"),
        }
        notes = []
        if building["residential_units"] == 0:
            notes.append("No residential units on record: this may not be an apartment building.")
        return building, notes

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


def _int(value: str | None) -> int | None:
    return int(float(value)) if value else None
