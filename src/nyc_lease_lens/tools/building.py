import json
import re

import requests

from nyc_lease_lens import opendata

PLUTO = "64uk-42ks"
BOROUGHS = ["Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"]


def lookup_building(address: str, borough: str | None = None) -> str:
    """Resolve an NYC address to its BBL/BIN and basic building facts."""
    try:
        features = opendata.geosearch(address)
    except requests.RequestException as e:
        return json.dumps({"error": f"Address lookup failed: {e}"})

    zip_code = re.search(r"\b1\d{4}\b", address)
    candidates = [
        c for c in map(_candidate, features)
        if c
        and (not borough or c["borough"].lower() == borough.lower())
        and (not zip_code or c["zip"] == zip_code.group())
    ]
    if not candidates:
        return json.dumps({"error": f"No NYC building found for '{address}'. Check the house number and street."})

    # GeoSearch ranks on text alone, so the same street address in two boroughs ties.
    same_address = {c["bbl"]: c for c in candidates if c["name"] == candidates[0]["name"]}
    if len(same_address) > 1:
        return json.dumps({
            "ambiguous": True,
            "message": "This address exists in more than one borough. Ask the user which one they mean.",
            "candidates": [{k: c[k] for k in ("address", "borough", "zip")} for c in same_address.values()],
        })

    place = candidates[0]
    del place["name"]
    notes = []

    try:
        rows = opendata.socrata(PLUTO, {"$where": f"bbl={place['bbl']}"})
    except requests.RequestException as e:
        rows = []
        notes.append(f"Building facts unavailable: PLUTO lookup failed ({e}).")

    if rows:
        place["building"] = _building_facts(rows[0])
        if place["building"]["residential_units"] == 0:
            notes.append("No residential units on record: this may not be an apartment building.")
    elif not notes:
        notes.append("No PLUTO record for this lot (common for condos), so building facts are unavailable.")

    if notes:
        place["notes"] = notes
    return json.dumps(place)


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


def _building_facts(row: dict) -> dict:
    return {
        "year_built": _int(row.get("yearbuilt")) or None,  # PLUTO uses 0 for unknown
        "floors": _int(row.get("numfloors")),
        "residential_units": _int(row.get("unitsres")),
        "total_units": _int(row.get("unitstotal")),
        "building_class": row.get("bldgclass"),
        "owner_of_record": row.get("ownername"),
    }


def _int(value: str | None) -> int | None:
    return int(float(value)) if value else None


SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_building",
        "description": (
            "Identify an NYC building from a street address. Returns its BBL (tax lot ID) and BIN "
            "(building ID), coordinates, and facts such as year built and number of apartments. "
            "Call this first for any address. If the result is ambiguous, ask the user which borough."
        ),
        "parameters": {
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
        },
    },
}
