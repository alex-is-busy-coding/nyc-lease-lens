from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

GRADES = [(10, "A"), (25, "B"), (45, "C"), (70, "D")]  # upper bounds; 70+ is F
SCALE = "0 (no red flags) to 100. A: 0-9, B: 10-24, C: 25-44, D: 45-69, F: 70+"
COMPLAINT_CAP = 18
COMPLAINT_LABELS = {
    "heat_hot_water": "heat/hot water",
    "pests": "pest",
    "mold": "mold",
    "leaks_plumbing": "leak/plumbing",
    "noise": "noise",
}


@dataclass
class Finding:
    points: int
    finding: str
    source: str


def score(
    building: dict[str, Any],
    violations: dict[str, Any] | None,
    complaints: dict[str, Any] | None,
    landlord: dict[str, Any] | None,
    history: dict[str, Any] | None,
) -> dict[str, Any]:
    """Grade a building from the check results. No network calls, so the weights are easy to test."""
    units = _units(building, complaints)
    flags: list[Finding] = []
    good: list[str] = []
    for part, check in (
        (violations, _violations),
        (complaints, _complaints),
        (landlord, _landlord),
        (history, _history),
    ):
        if part:
            check(part, units, flags, good)

    total = min(100, sum(f.points for f in flags))
    return {
        "grade": next((grade for bound, grade in GRADES if total < bound), "F"),
        "score": total,
        "scale": SCALE,
        "apartments_used_for_rates": units,
        "red_flags": [asdict(f) for f in sorted(flags, key=lambda f: f.points, reverse=True)],
        "good_signs": good,
    }


def _violations(v: dict, units: int | None, flags: list[Finding], good: list[str]) -> None:
    source = "HPD violations"
    open_now = v.get("open_now", {})
    class_c = open_now.get("C", 0)
    if class_c:
        rate = _per_100(class_c, units)
        # Small buildings: one old violation shouldn't score like hundreds, so cap by the raw count too.
        points = min(_rate_points(rate), _count_points(class_c))
        rate_text = f" ({rate:.0f} per 100 apartments)" if rate is not None else ""
        flags.append(Finding(points, f"{class_c} open class C (immediately hazardous) violations{rate_text}", source))
    elif not open_now.get("total"):
        good.append("No open HPD violations.")
    else:
        good.append("No open class C (immediately hazardous) violations.")

    if rent_impairing := v.get("rent_impairing_open", 0):
        rate = _per_100(rent_impairing, units)
        points = 10 if (rate or 0) >= 5 or rent_impairing >= 20 else 5
        flags.append(Finding(points, f"{rent_impairing} open rent-impairing violations", source))

    if oldest := v.get("oldest_open_hazardous"):
        days = oldest.get("days_open", 0)
        if days > 365:
            years = days / 365
            what = oldest.get("description", "")[:80]
            text = f"Oldest open hazardous violation has been open {years:.0f} years ({what})"
            flags.append(Finding(8 if years >= 5 else 5, text, source))

    categories = {c["category"]: c for c in v.get("categories", [])}
    if (illegal := categories.get("illegal_occupancy")) and illegal.get("open"):
        flags.append(
            Finding(10, f"{illegal['open']} open illegal-occupancy violations: apartments may not be legal", source)
        )
    if (lead := categories.get("lead_paint")) and lead.get("open_class_c"):
        flags.append(Finding(5, f"{lead['open_class_c']} open class C lead paint violations", source))


def _complaints(c: dict, units: int | None, flags: list[Finding], good: list[str]) -> None:
    source = "311 complaints"
    habitability = []
    for row in c.get("categories", []):
        percentile, category = row.get("percentile_vs_nearby"), row["category"]
        if percentile is None:
            continue
        text = _versus_neighbors(COMPLAINT_LABELS.get(category, category), percentile)
        if category in COMPLAINT_LABELS and category != "noise" and percentile >= 80:
            habitability.append(Finding(6 if percentile >= 95 else 3, text, source))
        elif category == "noise" and percentile >= 95:
            flags.append(Finding(3, text, source))
    capped = COMPLAINT_CAP
    for finding in sorted(habitability, key=lambda f: f.points, reverse=True):
        finding.points = min(finding.points, capped)
        capped -= finding.points
        if finding.points:
            flags.append(finding)

    seasons = c.get("heat_seasons") or []
    if seasons:
        recent_days = max(s.get("days_with_complaints", 0) for s in seasons[-2:])
        bad_winters = sum(s.get("days_with_complaints", 0) >= 20 for s in seasons)
        if recent_days >= 20:
            flags.append(
                Finding(
                    12 if recent_days >= 60 else 6,
                    f"Heat/hot water complaints on {recent_days} days in a recent winter",
                    source,
                )
            )
        if bad_winters >= 2:
            flags.append(Finding(4, f"Heat problems in {bad_winters} of the last {len(seasons)} winters", source))
        if not any(s.get("complaints") for s in seasons):
            good.append(f"No heat/hot water complaints in the last {len(seasons)} winters.")

    rated = [r for r in c.get("categories", []) if r.get("percentile_vs_nearby") is not None]
    if rated and all(r["percentile_vs_nearby"] < 50 for r in rated):
        good.append("Fewer 311 complaints per apartment than most nearby buildings.")


def _landlord(profile: dict, units: int | None, flags: list[Finding], good: list[str]) -> None:
    source = "HPD registrations"
    if profile.get("registered") is False:
        if (units or 0) >= 3:
            flags.append(
                Finding(5, "Not registered with HPD, although buildings with 3+ apartments must register", source)
            )
        return
    if profile.get("registration", {}).get("status") == "lapsed":
        flags.append(Finding(5, "HPD registration has lapsed", source))

    portfolios = [profile[k] for k in ("portfolio", "owner_portfolio", "management_portfolio") if k in profile]
    rates = [p["times_citywide_rate"] for p in portfolios if p.get("times_citywide_rate") is not None]
    if rates:
        worst = max(rates)
        if worst >= 2:
            flags.append(
                Finding(10, f"Landlord's buildings average {worst}x the citywide rate of hazardous violations", source)
            )
        elif worst >= 1.25:
            flags.append(
                Finding(5, f"Landlord's buildings average {worst}x the citywide rate of hazardous violations", source)
            )
        elif worst <= 0.5:
            good.append(f"Landlord's buildings average {worst}x the citywide rate of hazardous violations.")
    if aep := max((p.get("buildings_in_enforcement_program", 0) for p in portfolios), default=0):
        flags.append(
            Finding(
                5,
                f"{aep} of the landlord's buildings are in the city's program for the worst-maintained buildings",
                source,
            )
        )


def _history(h: dict, units: int | None, flags: list[Finding], good: list[str]) -> None:
    for order in h.get("vacate_orders", {}).get("in_effect", []):
        whole = order.get("scope") == "Entire Building"
        flags.append(
            Finding(
                30 if whole else 20,
                f"{order.get('scope', 'Partial')} vacate order in effect since {order.get('since')} "
                f"({order.get('reason')}, {order.get('address')})",
                "HPD vacate orders",
            )
        )

    court = h.get("housing_court", {})
    ten_years_ago = date(date.today().year - 10, 1, 1).isoformat()
    for finding in court.get("harassment_findings", []):
        recent = (finding.get("decided") or "") >= ten_years_ago
        flags.append(
            Finding(
                25 if recent else 10,
                f"Housing court found tenant harassment ({finding.get('decided')})",
                "HPD litigation",
            )
        )
    if administrator := court.get("court_appointed_administrator"):
        recent = any(d >= ten_years_ago for d in administrator.get("opened", []))
        flags.append(
            Finding(
                15 if recent else 5,
                f"A court appointed an administrator to run the building ({administrator['cases']} case(s), "
                f"latest {max(administrator.get('opened') or ['date unknown'])})",
                "HPD litigation",
            )
        )
    if false_certs := court.get("cases", {}).get("false_repair_certifications"):
        flags.append(
            Finding(8, f"{false_certs} case(s) of the landlord certifying repairs that weren't made", "HPD litigation")
        )
    if not any(
        (
            h.get("vacate_orders", {}).get("in_effect"),
            court.get("harassment_findings"),
            court.get("court_appointed_administrator"),
        )
    ):
        good.append("No vacate orders in effect, harassment findings or court-appointed administrators.")

    evictions = h.get("evictions", {}).get("total", 0)
    years = max(1, date.today().year - int(h.get("counting_since", str(date.today().year))[:4]))
    if evictions and (rate := _per_100(evictions / years, units)) is not None and rate >= 1:
        flags.append(
            Finding(
                8 if rate >= 2 else 4,
                f"{evictions} evictions in {years} years ({rate:.1f} per 100 apartments a year)",
                "Marshal evictions",
            )
        )

    reports = h.get("bedbug_reports", [])
    if missing := [r["period_start"] for r in reports if not r.get("filed")]:
        flags.append(Finding(4, f"Missing required bedbug report(s) for {', '.join(missing)}", "HPD bedbug reports"))
    filed = [r for r in reports if r.get("filed")]
    if filed and filed[0].get("apartments"):
        share = filed[0].get("infested", 0) / filed[0]["apartments"]
        if share >= 0.05:
            flags.append(
                Finding(5, f"Bedbugs reported in {share:.0%} of apartments in the latest report", "HPD bedbug reports")
            )
        elif not missing and all(r.get("infested", 0) == 0 for r in filed):
            good.append("No bedbugs reported in recent bedbug reports.")


def _versus_neighbors(label: str, percentile: int) -> str:
    than = "any nearby building" if percentile >= 100 else f"{percentile}% of nearby buildings"
    return f"More {label} complaints per apartment than {than}"


def _units(building: dict[str, Any], complaints: dict[str, Any] | None) -> int | None:
    facts = building.get("building") or {}
    return (
        (complaints or {}).get("residential_units")
        or facts.get("residential_units_on_lot")
        or facts.get("apartments_in_building")
        or None
    )


def _per_100(count: float, units: int | None) -> float | None:
    return 100 * count / units if units else None


def _rate_points(per_100: float | None) -> int:
    if per_100 is None:  # unit count unknown: let the raw count decide
        return 30
    return 30 if per_100 >= 50 else 20 if per_100 >= 20 else 12 if per_100 >= 5 else 5


def _count_points(count: int) -> int:
    return 30 if count >= 20 else 20 if count >= 5 else 12 if count >= 2 else 5
