import logging
import re
from datetime import date
from typing import Any

import requests

from nyc_lease_lens.data import datasets
from nyc_lease_lens.data.parsing import to_int
from nyc_lease_lens.data.soql import quote
from nyc_lease_lens.observability.context import ContextThreadPoolExecutor
from nyc_lease_lens.rules import PORTFOLIO_MAX_REGISTRATIONS
from nyc_lease_lens.tools.base import Tool

logger = logging.getLogger(__name__)


class GetLandlordProfile(Tool):
    """Identify who owns and manages a building, and how their other buildings are kept."""

    name = "get_landlord_profile"
    error_label = "Landlord lookup"
    data_sources = (
        datasets.HPD_REGISTRATIONS,
        datasets.HPD_CONTACTS,
        datasets.HPD_BUILDINGS,
        datasets.HPD_VIOLATIONS,
        datasets.AEP,
    )
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
        bin = self.validate_bin(bin)
        registrations = self.query(
            datasets.HPD_REGISTRATIONS,
            {"$where": f"bin='{bin}'", "$order": "lastregistrationdate DESC", "$limit": 1},
        )
        if not registrations:
            logger.info("no HPD registration", extra={"bin": bin})
            return {
                "bin": bin,
                "registered": False,
                "notes": [
                    "No HPD registration for this building. Buildings with 3 or more apartments must "
                    "register every year; 1-2 family homes usually don't have to."
                ],
            }

        registration = registrations[0]
        contacts = self.query(datasets.HPD_CONTACTS, {"$where": f"registrationid='{registration['registrationid']}'"})
        people = _key_contacts(contacts)
        result: dict[str, Any] = {"bin": bin, "registration": _registration_status(registration, self._data_as_of())}
        result |= {k: v["label"] for k, v in people.items()}
        notes: list[str] = []
        if result["registration"]["status"] == "lapsed":
            notes.append(
                "The registration has lapsed. Owners who don't register can't take tenants to "
                "housing court for unpaid rent until they do."
            )

        owner_ids, agent_ids = self._add_portfolios(people, result)
        if len(owner_ids) >= PORTFOLIO_MAX_REGISTRATIONS or len(agent_ids) >= PORTFOLIO_MAX_REGISTRATIONS:
            notes.append(f"Portfolio capped at {PORTFOLIO_MAX_REGISTRATIONS} registrations; totals are a lower bound.")
        if notes:
            result["notes"] = notes
        logger.debug(
            "landlord profiled",
            extra={
                "bin": bin,
                "registration": result["registration"]["status"],
                "owner_registrations": len(owner_ids),
                "agent_registrations": len(agent_ids),
            },
        )
        return result

    def _add_portfolios(self, people: dict[str, dict], result: dict[str, Any]) -> tuple[set[str], set[str]]:
        """Summarize the owner's and the managing agent's portfolios, merged if they're the same buildings."""
        with ContextThreadPoolExecutor() as pool:
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
        return owner_ids, agent_ids

    def _portfolio(self, where: str) -> tuple[set[str], list[dict]]:
        rows = self.query(
            datasets.HPD_CONTACTS,
            {"$select": "distinct registrationid", "$where": where, "$limit": PORTFOLIO_MAX_REGISTRATIONS},
        )
        ids = {r["registrationid"] for r in rows}
        registrations = self.query_in(
            datasets.HPD_REGISTRATIONS,
            "registrationid",
            sorted(ids),
            {"$select": "buildingid, housenumber, streetname, boro"},
        )
        return ids, registrations

    def _summarize(self, registrations: list[dict], citywide_rate: float | None) -> dict[str, Any]:
        buildings = {r["buildingid"]: r for r in registrations if r.get("buildingid")}
        ids = sorted(buildings)
        with ContextThreadPoolExecutor() as pool:
            units_future = pool.submit(
                self.query_in,
                datasets.HPD_BUILDINGS,
                "buildingid",
                ids,
                {"$select": "buildingid, legalclassa", "$where": "recordstatus='Active'"},
            )
            violations_future = pool.submit(
                self.query_in,
                datasets.HPD_VIOLATIONS,
                "buildingid",
                ids,
                {
                    "$select": "buildingid, count(*) AS n",
                    "$where": "violationstatus='Open' AND class='C'",
                    "$group": "buildingid",
                },
            )
            aep_future = pool.submit(
                self.query_in, datasets.AEP, "building_id", ids, {"$select": "building_id, current_status"}
            )
            units = {r["buildingid"]: to_int(r.get("legalclassa")) or 0 for r in units_future.result()}
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
                    datasets.HPD_VIOLATIONS,
                    {"$select": "count(*) AS n", "$where": "violationstatus='Open' AND class='C'"},
                )
                units = self.client.socrata(
                    datasets.HPD_BUILDINGS, {"$select": "sum(legalclassa) AS n", "$where": "recordstatus='Active'"}
                )
                GetLandlordProfile._citywide_rate = 100 * int(violations[0]["n"]) / float(units[0]["n"])
                logger.info(
                    "citywide violation rate cached", extra={"per_100": round(GetLandlordProfile._citywide_rate, 1)}
                )
            except (requests.RequestException, KeyError, IndexError, ValueError, ZeroDivisionError) as e:
                logger.warning("citywide violation rate unavailable", extra={"error": repr(e)[:200]})
                return None
        return GetLandlordProfile._citywide_rate

    def _data_as_of(self) -> str:
        """Newest registration date in the dataset. Renewals show up weeks late."""
        if GetLandlordProfile._registrations_as_of is None:
            try:
                rows = self.client.socrata(
                    datasets.HPD_REGISTRATIONS, {"$select": "max(lastregistrationdate) AS latest"}
                )
                GetLandlordProfile._registrations_as_of = rows[0]["latest"][:10]
                logger.info("registration data date cached", extra={"as_of": GetLandlordProfile._registrations_as_of})
            except (requests.RequestException, KeyError, IndexError) as e:
                logger.warning("registration data date unavailable; using today", extra={"error": repr(e)[:200]})
                return date.today().isoformat()
        return GetLandlordProfile._registrations_as_of


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
        f"AND upper(firstname)={quote((officer.get('firstname') or '').upper())} "
        f"AND upper(lastname)={quote(officer['lastname'].upper())}"
    )
    if officer.get("businesszip"):
        where += f" AND businesszip={quote(officer['businesszip'])}"
    return where


def _agent_filter(people: dict[str, dict]) -> str:
    agent = people["managing_agent"]["contact"]
    if corporation := agent.get("corporationname"):
        return f"type='Agent' AND upper(corporationname)={quote(corporation.upper())}"
    return (
        f"type='Agent' AND upper(firstname)={quote((agent.get('firstname') or '').upper())} "
        f"AND upper(lastname)={quote((agent.get('lastname') or '').upper())}"
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
