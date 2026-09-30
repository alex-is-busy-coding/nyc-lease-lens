import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import requests

from nyc_lease_lens.tools.base import Tool, ToolError
from nyc_lease_lens.tools.building import HPD_BUILDINGS
from nyc_lease_lens.tools.violations import HPD_VIOLATIONS

HPD_REGISTRATIONS = "tesw-yqqr"
HPD_CONTACTS = "feu5-w2e2"
AEP = "hcir-3275"
CHUNK = 300
MAX_REGISTRATIONS = 3000


class GetLandlordProfile(Tool):
    """Identify who owns and manages a building, and how their other buildings are kept."""

    name = "get_landlord_profile"
    description = (
        "Identify the building's registered owner, head officer and managing agent from HPD "
        "registrations, and summarize their portfolios: how many buildings and apartments they "
        "control, open class C (immediately hazardous) violations per 100 apartments compared "
        "with the citywide rate, buildings in the city's Alternative Enforcement Program for "
        "the worst-maintained buildings, and their worst buildings. Use the bin from lookup_building."
    )
    parameters = {
        "type": "object",
        "properties": {"bin": {"type": "string", "description": "7-digit BIN from lookup_building"}},
        "required": ["bin"],
    }

    _citywide_rate: float | None = None
    _registrations_as_of: str | None = None

    def run(self, bin: str) -> dict[str, Any]:
        if not re.fullmatch(r"\d{7}", bin):
            raise ToolError(f"'{bin}' is not a 7-digit BIN. Call lookup_building first.")

        registrations = self._query(
            HPD_REGISTRATIONS,
            {"$where": f"bin='{bin}'", "$order": "lastregistrationdate DESC", "$limit": 1},
        )
        if not registrations:
            return {
                "bin": bin,
                "registered": False,
                "notes": [
                    "No HPD registration for this building. Buildings with 3 or more apartments must "
                    "register every year; 1-2 family homes usually don't have to."
                ],
            }

        registration = registrations[0]
        contacts = self._query(HPD_CONTACTS, {"$where": f"registrationid='{registration['registrationid']}'"})
        people = _key_contacts(contacts)
        result: dict[str, Any] = {"bin": bin, "registration": _registration_status(registration, self._data_as_of())}
        result |= {k: v["label"] for k, v in people.items()}
        notes: list[str] = []
        if result["registration"]["status"] == "lapsed":
            notes.append(
                "The registration has lapsed. Owners who don't register can't take tenants to "
                "housing court for unpaid rent until they do."
            )

        with ThreadPoolExecutor() as pool:
            citywide = pool.submit(self._citywide)
            owner = pool.submit(self._portfolio, _owner_filter(people)) if "head_officer" in people else None
            agent = pool.submit(self._portfolio, _agent_filter(people)) if "managing_agent" in people else None
            rate = citywide.result()
            owner_ids, owner_regs = owner.result() if owner else (set(), [])
            agent_ids, agent_regs = agent.result() if agent else (set(), [])

        if owner_ids and owner_ids == agent_ids:
            result["portfolio"] = self._summarize(owner_regs, rate) | {"linked_by": "head officer and managing agent"}
        else:
            if owner_ids:
                result["owner_portfolio"] = self._summarize(owner_regs, rate) | {
                    "linked_by": "same head officer and office ZIP"
                }
            if agent_ids:
                result["management_portfolio"] = self._summarize(agent_regs, rate) | {
                    "linked_by": "same managing agent company"
                }
        if len(owner_ids) >= MAX_REGISTRATIONS or len(agent_ids) >= MAX_REGISTRATIONS:
            notes.append(f"Portfolio capped at {MAX_REGISTRATIONS} registrations; totals are a lower bound.")
        if notes:
            result["notes"] = notes
        return result

    def _portfolio(self, where: str) -> tuple[set[str], list[dict]]:
        rows = self._query(
            HPD_CONTACTS,
            {"$select": "distinct registrationid", "$where": where, "$limit": MAX_REGISTRATIONS},
        )
        ids = {r["registrationid"] for r in rows}
        registrations = self._chunked(
            HPD_REGISTRATIONS,
            "registrationid",
            sorted(ids),
            {"$select": "buildingid, housenumber, streetname, boro"},
        )
        return ids, registrations

    def _summarize(self, registrations: list[dict], citywide_rate: float | None) -> dict[str, Any]:
        buildings = {r["buildingid"]: r for r in registrations if r.get("buildingid")}
        ids = sorted(buildings)
        with ThreadPoolExecutor() as pool:
            units_future = pool.submit(
                self._chunked,
                HPD_BUILDINGS,
                "buildingid",
                ids,
                {"$select": "buildingid, legalclassa", "$where": "recordstatus='Active'"},
            )
            violations_future = pool.submit(
                self._chunked,
                HPD_VIOLATIONS,
                "buildingid",
                ids,
                {
                    "$select": "buildingid, count(*) AS n",
                    "$where": "violationstatus='Open' AND class='C'",
                    "$group": "buildingid",
                },
            )
            aep_future = pool.submit(self._chunked, AEP, "building_id", ids, {"$select": "building_id, current_status"})
            units = {r["buildingid"]: int(r.get("legalclassa") or 0) for r in units_future.result()}
            open_c = {r["buildingid"]: int(r["n"]) for r in violations_future.result()}
            aep = [r for r in aep_future.result() if "active" in r.get("current_status", "").lower()]

        total_units, total_c = sum(units.values()), sum(open_c.values())
        summary: dict[str, Any] = {"buildings": len(buildings), "apartments": total_units, "open_class_c": total_c}
        if total_units:
            per_100 = 100 * total_c / total_units
            summary["open_class_c_per_100_apartments"] = round(per_100, 1)
            if citywide_rate:
                summary["citywide_per_100_apartments"] = round(citywide_rate, 1)
                summary["times_citywide_rate"] = round(per_100 / citywide_rate, 1)
        summary["buildings_in_enforcement_program"] = len({r["building_id"] for r in aep})
        worst = sorted(open_c, key=lambda b: open_c[b], reverse=True)[:3]
        summary["worst_buildings"] = [
            {"address": _address(buildings[b]), "open_class_c": open_c[b], "apartments": units.get(b)}
            for b in worst
            if open_c[b]
        ]
        return summary

    def _citywide(self) -> float | None:
        """Open class C violations per 100 registered apartments, citywide. Cached for the process."""
        if GetLandlordProfile._citywide_rate is None:
            try:
                violations = self.client.socrata(
                    HPD_VIOLATIONS,
                    {"$select": "count(*) AS n", "$where": "violationstatus='Open' AND class='C'"},
                )
                units = self.client.socrata(
                    HPD_BUILDINGS, {"$select": "sum(legalclassa) AS n", "$where": "recordstatus='Active'"}
                )
                GetLandlordProfile._citywide_rate = 100 * int(violations[0]["n"]) / float(units[0]["n"])
            except (requests.RequestException, KeyError, IndexError, ValueError, ZeroDivisionError):
                return None
        return GetLandlordProfile._citywide_rate

    def _data_as_of(self) -> str:
        """Newest registration date in the dataset. Renewals show up weeks late."""
        if GetLandlordProfile._registrations_as_of is None:
            try:
                rows = self.client.socrata(HPD_REGISTRATIONS, {"$select": "max(lastregistrationdate) AS latest"})
                GetLandlordProfile._registrations_as_of = rows[0]["latest"][:10]
            except (requests.RequestException, KeyError, IndexError):
                return date.today().isoformat()
        return GetLandlordProfile._registrations_as_of

    def _chunked(self, dataset: str, field: str, values: list[str], params: dict) -> list[dict]:
        chunks = [values[i : i + CHUNK] for i in range(0, len(values), CHUNK)]

        def fetch(chunk: list[str]) -> list[dict]:
            where = f"{field} in ({','.join(_quote(v) for v in chunk)})"
            if extra := params.get("$where"):
                where = f"{extra} AND {where}"
            return self._query(dataset, params | {"$where": where, "$limit": 50000})

        with ThreadPoolExecutor(max_workers=4) as pool:
            return [row for rows in pool.map(fetch, chunks) for row in rows]

    def _query(self, dataset: str, params: dict) -> list[dict]:
        try:
            return self.client.socrata(dataset, params)
        except requests.RequestException as e:
            raise ToolError(f"Landlord lookup failed: {e}") from e


def _key_contacts(contacts: list[dict]) -> dict[str, dict]:
    """Owner entity, head officer and managing agent company. Staff names are left out."""
    by_type: dict[str, dict] = {}
    for contact in contacts:
        by_type.setdefault(contact.get("type", ""), contact)

    people: dict[str, dict] = {}
    if owner := by_type.get("CorporateOwner"):
        people["owner"] = {"label": _clean(owner.get("corporationname"))}
    elif owner := by_type.get("IndividualOwner"):
        people["owner"] = {"label": _person(owner)}
    officer = by_type.get("HeadOfficer") or by_type.get("IndividualOwner")
    if officer and officer.get("lastname"):
        people["head_officer"] = {"label": _person(officer), "contact": officer}
    if agent := by_type.get("Agent"):
        label = _clean(agent.get("corporationname")) or _person(agent)
        if label:
            people["managing_agent"] = {"label": label, "contact": agent}
    return people


def _owner_filter(people: dict[str, dict]) -> str:
    officer = people["head_officer"]["contact"]
    where = (
        "type in ('HeadOfficer', 'IndividualOwner') "
        f"AND upper(firstname)={_quote((officer.get('firstname') or '').upper())} "
        f"AND upper(lastname)={_quote(officer['lastname'].upper())}"
    )
    if officer.get("businesszip"):
        where += f" AND businesszip={_quote(officer['businesszip'])}"
    return where


def _agent_filter(people: dict[str, dict]) -> str:
    agent = people["managing_agent"]["contact"]
    if corporation := agent.get("corporationname"):
        return f"type='Agent' AND upper(corporationname)={_quote(corporation.upper())}"
    return (
        f"type='Agent' AND upper(firstname)={_quote((agent.get('firstname') or '').upper())} "
        f"AND upper(lastname)={_quote((agent.get('lastname') or '').upper())}"
    )


def _registration_status(registration: dict, data_as_of: str) -> dict[str, Any]:
    ends = (registration.get("registrationenddate") or "")[:10]
    if ends and ends >= date.today().isoformat():
        status = "current"
    elif ends and ends > data_as_of:
        status = "renewal_not_yet_in_data"
    else:
        status = "lapsed"
    return {
        "last_registered": (registration.get("lastregistrationdate") or "")[:10] or None,
        "registered_until": ends or None,
        "status": status,
        "data_as_of": data_as_of,
    }


def _address(registration: dict) -> str:
    parts = (registration.get("housenumber"), registration.get("streetname"), registration.get("boro"))
    return " ".join(p for p in parts if p).title()


def _person(contact: dict) -> str:
    return " ".join(p for p in (contact.get("firstname"), contact.get("lastname")) if p).strip().upper()


def _clean(name: str | None) -> str:
    return re.sub(r"[^\w&.,' -]", "", name or "").strip()


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
