from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

GRADES = [(10, "A"), (25, "B"), (45, "C"), (70, "D")]  # upper bounds; 70+ is F


def grade_bands() -> list[tuple[str, int, int]]:
    """(grade, lowest score, highest score) for each grade, A to F."""
    lows = [0] + [bound for bound, _ in GRADES]
    highs = [bound - 1 for bound, _ in GRADES] + [100]
    return list(zip([g for _, g in GRADES] + ["F"], lows, highs, strict=True))


SCALE = "0 (no red flags) to 100. " + ", ".join(f"{g}: {lo}-{hi}" for g, lo, hi in grade_bands())
RECENT_YEARS = 10  # harassment findings and court-appointed administrators count less after this
COMPLAINT_LABELS = {
    "heat_hot_water": "heat/hot water",
    "pests": "pest",
    "mold": "mold",
    "leaks_plumbing": "leak/plumbing",
    "noise": "noise",
}

Part = Literal["violations", "complaints", "landlord", "history"]
Tiers = tuple[tuple[float, int], ...]  # (at least this value, points), highest first


@dataclass(frozen=True)
class Facts:
    """Everything the rules look at: the check results plus the apartment count and today's date."""

    building: dict[str, Any]
    violations: dict[str, Any]
    complaints: dict[str, Any]
    landlord: dict[str, Any]
    history: dict[str, Any]
    units: int | None
    today: date

    @property
    def recent_since(self) -> str:
        return date(self.today.year - RECENT_YEARS, 1, 1).isoformat()


@dataclass(frozen=True)
class Hit:
    """One thing a rule found. `value` is matched against the tiers; None means unknown."""

    value: float | None
    text: str
    count: float | None = None  # matched against count_tiers, if the rule has them


@dataclass(frozen=True)
class Rule:
    """A red flag: what it measures, how many points each level is worth, and where the data comes from."""

    signal: str  # what the README calls it
    source: str
    part: Part  # which check's results it reads; skipped when that check has no results
    find: Callable[[Facts], Iterable[Hit]]
    tiers: Tiers
    unit: str = ""
    threshold_format: str | Callable[[float], str] = "{:g}+"  # how the README shows a threshold
    count_tiers: Tiers = ()
    combine: Literal["lower", "higher"] = "lower"  # how value and count points combine
    cap: int | None = None  # most points this rule's hits can add up to

    def points(self, hit: Hit) -> int:
        known = [p for p in (_tier(self.tiers, hit.value), _tier(self.count_tiers, hit.count)) if p is not None]
        return (min if self.combine == "lower" else max)(known, default=0)


@dataclass(frozen=True)
class GoodSign:
    part: Part
    find: Callable[[Facts], str | None]


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
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Grade a building from the check results. No network calls, so the rules are easy to test."""
    facts = Facts(
        building=building,
        violations=violations or {},
        complaints=complaints or {},
        landlord=landlord or {},
        history=history or {},
        units=_units(building, complaints),
        today=today or date.today(),
    )
    flags = [finding for rule in RULES if getattr(facts, rule.part) for finding in _apply(rule, facts)]
    good = [text for sign in GOOD_SIGNS if getattr(facts, sign.part) and (text := sign.find(facts))]

    total = min(100, sum(f.points for f in flags))
    return {
        "grade": next((grade for bound, grade in GRADES if total < bound), "F"),
        "score": total,
        "scale": SCALE,
        "apartments_used_for_rates": facts.units,
        "red_flags": [asdict(f) for f in sorted(flags, key=lambda f: f.points, reverse=True)],
        "good_signs": good,
    }


def _apply(rule: Rule, facts: Facts) -> list[Finding]:
    scored = [(points, hit) for hit in rule.find(facts) if (points := rule.points(hit)) > 0]
    if rule.cap is not None:
        remaining, capped = rule.cap, []
        for points, hit in sorted(scored, key=lambda s: s[0], reverse=True):
            points = min(points, remaining)
            remaining -= points
            if points:
                capped.append((points, hit))
        scored = capped
    return [Finding(points, hit.text, rule.source) for points, hit in scored]


def _tier(tiers: Tiers, value: float | None) -> int | None:
    if not tiers or value is None:
        return None
    return next((points for at_least, points in tiers if value >= at_least), 0)


# --- What each rule reads from the check results -------------------------------------------


def _class_c(f: Facts) -> Iterable[Hit]:
    if class_c := f.violations.get("open_now", {}).get("C", 0):
        rate = _per_100(class_c, f.units)
        rate_text = f" ({rate:.0f} per 100 apartments)" if rate is not None else ""
        yield Hit(rate, f"{class_c} open class C (immediately hazardous) violations{rate_text}", count=class_c)


def _rent_impairing(f: Facts) -> Iterable[Hit]:
    if count := f.violations.get("rent_impairing_open", 0):
        yield Hit(_per_100(count, f.units), f"{count} open rent-impairing violations", count=count)


def _oldest_hazard(f: Facts) -> Iterable[Hit]:
    if oldest := f.violations.get("oldest_open_hazardous"):
        days = oldest.get("days_open", 0)
        what = oldest.get("description", "")[:80]
        yield Hit(days, f"Oldest open hazardous violation has been open {days / 365:.0f} years ({what})")


def _violation_category(category: str, field: str, text: str) -> Callable[[Facts], Iterable[Hit]]:
    def find(f: Facts) -> Iterable[Hit]:
        row = next((c for c in f.violations.get("categories", []) if c["category"] == category), None)
        if row and (count := row.get(field)):
            yield Hit(count, text.format(count=count))

    return find


def _versus_neighbors(categories: tuple[str, ...]) -> Callable[[Facts], Iterable[Hit]]:
    def find(f: Facts) -> Iterable[Hit]:
        for row in f.complaints.get("categories", []):
            percentile = row.get("percentile_vs_nearby")
            if row["category"] in categories and percentile is not None:
                than = "any nearby building" if percentile >= 100 else f"{percentile}% of nearby buildings"
                label = COMPLAINT_LABELS[row["category"]]
                yield Hit(percentile, f"More {label} complaints per apartment than {than}")

    return find


def _heat_days(f: Facts) -> Iterable[Hit]:
    if seasons := f.complaints.get("heat_seasons"):
        days = max(s.get("days_with_complaints", 0) for s in seasons[-2:])
        yield Hit(days, f"Heat/hot water complaints on {days} days in a recent winter")


def _heat_winters(f: Facts) -> Iterable[Hit]:
    if seasons := f.complaints.get("heat_seasons"):
        bad = sum(s.get("days_with_complaints", 0) >= 20 for s in seasons)
        yield Hit(bad, f"Heat problems in {bad} of the last {len(seasons)} winters")


def _unregistered(f: Facts) -> Iterable[Hit]:
    if f.landlord.get("registered") is False:
        yield Hit(f.units or 0, "Not registered with HPD, although buildings with 3+ apartments must register")


def _lapsed(f: Facts) -> Iterable[Hit]:
    if f.landlord.get("registration", {}).get("status") == "lapsed":
        yield Hit(1, "HPD registration has lapsed")


def _landlord_rate(f: Facts) -> Iterable[Hit]:
    if rates := [p["times_citywide_rate"] for p in _portfolios(f) if p.get("times_citywide_rate") is not None]:
        worst = max(rates)
        yield Hit(worst, f"Landlord's buildings average {worst}x the citywide rate of hazardous violations")


def _enforcement_program(f: Facts) -> Iterable[Hit]:
    if count := max((p.get("buildings_in_enforcement_program", 0) for p in _portfolios(f)), default=0):
        yield Hit(
            count, f"{count} of the landlord's buildings are in the city's program for the worst-maintained buildings"
        )


def _vacates(whole_building: bool) -> Callable[[Facts], Iterable[Hit]]:
    def find(f: Facts) -> Iterable[Hit]:
        for order in f.history.get("vacate_orders", {}).get("in_effect", []):
            if (order.get("scope") == "Entire Building") == whole_building:
                yield Hit(
                    1,
                    f"{order.get('scope', 'Partial')} vacate order in effect since {order.get('since')} "
                    f"({order.get('reason')}, {order.get('address')})",
                )

    return find


def _harassment(recent: bool) -> Callable[[Facts], Iterable[Hit]]:
    def find(f: Facts) -> Iterable[Hit]:
        for finding in f.history.get("housing_court", {}).get("harassment_findings", []):
            if ((finding.get("decided") or "") >= f.recent_since) == recent:
                yield Hit(1, f"Housing court found tenant harassment ({finding.get('decided')})")

    return find


def _administrator(recent: bool) -> Callable[[Facts], Iterable[Hit]]:
    def find(f: Facts) -> Iterable[Hit]:
        if case := f.history.get("housing_court", {}).get("court_appointed_administrator"):
            opened = case.get("opened", [])
            if any(d >= f.recent_since for d in opened) == recent:
                latest = max(opened or ["date unknown"])
                yield Hit(
                    1,
                    f"A court appointed an administrator to run the building "
                    f"({case['cases']} case(s), latest {latest})",
                )

    return find


def _false_certifications(f: Facts) -> Iterable[Hit]:
    if count := f.history.get("housing_court", {}).get("cases", {}).get("false_repair_certifications"):
        yield Hit(count, f"{count} case(s) of the landlord certifying repairs that weren't made")


def _evictions(f: Facts) -> Iterable[Hit]:
    evictions = f.history.get("evictions", {}).get("total", 0)
    years = max(1, f.today.year - int(f.history.get("counting_since", str(f.today.year))[:4]))
    if evictions and (rate := _per_100(evictions / years, f.units)) is not None:
        yield Hit(rate, f"{evictions} evictions in {years} years ({rate:.1f} per 100 apartments a year)")


def _missing_bedbug_reports(f: Facts) -> Iterable[Hit]:
    if missing := [r["period_start"] for r in f.history.get("bedbug_reports", []) if not r.get("filed")]:
        yield Hit(len(missing), f"Missing required bedbug report(s) for {', '.join(missing)}")


def _bedbug_share(f: Facts) -> Iterable[Hit]:
    filed = [r for r in f.history.get("bedbug_reports", []) if r.get("filed")]
    if filed and filed[0].get("apartments"):
        share = filed[0].get("infested", 0) / filed[0]["apartments"]
        yield Hit(share, f"Bedbugs reported in {share:.0%} of apartments in the latest report")


# --- The rules, in the order findings are listed when points tie ---------------------------

RULES: list[Rule] = [
    Rule(
        "Open class C (immediately hazardous) violations",
        "HPD violations",
        "violations",
        _class_c,
        tiers=((50, 30), (20, 20), (5, 12), (0, 5)),
        unit="per 100 apartments",
        # Small buildings: one old violation shouldn't score like hundreds, so the count caps the rate.
        count_tiers=((20, 30), (5, 20), (2, 12), (1, 5)),
        combine="lower",
    ),
    Rule(
        "Open rent-impairing violations",
        "HPD violations",
        "violations",
        _rent_impairing,
        tiers=((5, 10),),
        unit="per 100 apartments",
        count_tiers=((20, 10), (1, 5)),
        combine="higher",
    ),
    Rule(
        "Oldest open hazardous violation",
        "HPD violations",
        "violations",
        _oldest_hazard,
        tiers=((5 * 365, 8), (366, 5)),
        unit="open",
        threshold_format=lambda days: f"{days / 365:.0f}+ years",
    ),
    Rule(
        "Open illegal-occupancy violations",
        "HPD violations",
        "violations",
        _violation_category(
            "illegal_occupancy", "open", "{count} open illegal-occupancy violations: apartments may not be legal"
        ),
        tiers=((1, 10),),
    ),
    Rule(
        "Open class C lead paint violations",
        "HPD violations",
        "violations",
        _violation_category("lead_paint", "open_class_c", "{count} open class C lead paint violations"),
        tiers=((1, 5),),
    ),
    Rule(
        "Noise complaints compared with nearby buildings",
        "311 complaints",
        "complaints",
        _versus_neighbors(("noise",)),
        tiers=((95, 3),),
        unit="percentile",
    ),
    Rule(
        "Heat, pest, mold and leak complaints compared with nearby buildings, per category",
        "311 complaints",
        "complaints",
        _versus_neighbors(("heat_hot_water", "pests", "mold", "leaks_plumbing")),
        tiers=((95, 6), (80, 3)),
        unit="percentile",
        cap=18,
    ),
    Rule(
        "Days with heat complaints in the worse of the last two winters",
        "311 complaints",
        "complaints",
        _heat_days,
        tiers=((60, 12), (20, 6)),
        unit="days",
    ),
    Rule(
        "Winters with 20+ days of heat complaints",
        "311 complaints",
        "complaints",
        _heat_winters,
        tiers=((2, 4),),
        unit="winters",
    ),
    Rule(
        "Not registered with HPD",
        "HPD registrations",
        "landlord",
        _unregistered,
        tiers=((3, 5),),
        unit="apartments",
    ),
    Rule("HPD registration lapsed", "HPD registrations", "landlord", _lapsed, tiers=((1, 5),)),
    Rule(
        "Landlord's hazardous violation rate compared with the city",
        "HPD registrations",
        "landlord",
        _landlord_rate,
        tiers=((2, 10), (1.25, 5)),
        unit="the citywide rate",
        threshold_format="{:g}x+",
    ),
    Rule(
        "Landlord's buildings in the Alternative Enforcement Program",
        "HPD registrations",
        "landlord",
        _enforcement_program,
        tiers=((1, 5),),
    ),
    Rule(
        "Entire-building vacate order in effect",
        "HPD vacate orders",
        "history",
        _vacates(whole_building=True),
        tiers=((1, 30),),
    ),
    Rule(
        "Partial vacate order in effect",
        "HPD vacate orders",
        "history",
        _vacates(whole_building=False),
        tiers=((1, 20),),
    ),
    Rule(
        f"Harassment finding in the last {RECENT_YEARS} years",
        "HPD litigation",
        "history",
        _harassment(recent=True),
        tiers=((1, 25),),
    ),
    Rule(
        f"Harassment finding over {RECENT_YEARS} years ago",
        "HPD litigation",
        "history",
        _harassment(recent=False),
        tiers=((1, 10),),
    ),
    Rule(
        f"Court-appointed administrator in the last {RECENT_YEARS} years",
        "HPD litigation",
        "history",
        _administrator(recent=True),
        tiers=((1, 15),),
    ),
    Rule(
        f"Court-appointed administrator over {RECENT_YEARS} years ago",
        "HPD litigation",
        "history",
        _administrator(recent=False),
        tiers=((1, 5),),
    ),
    Rule(
        "Landlord certified repairs that weren't made",
        "HPD litigation",
        "history",
        _false_certifications,
        tiers=((1, 8),),
    ),
    Rule(
        "Evictions",
        "Marshal evictions",
        "history",
        _evictions,
        tiers=((2, 8), (1, 4)),
        unit="per 100 apartments a year",
    ),
    Rule(
        "Missing required bedbug reports",
        "HPD bedbug reports",
        "history",
        _missing_bedbug_reports,
        tiers=((1, 4),),
    ),
    Rule(
        "Apartments with bedbugs in the latest report",
        "HPD bedbug reports",
        "history",
        _bedbug_share,
        tiers=((0.05, 5),),
        unit="of apartments",
        threshold_format="{:.0%}+",
    ),
]


# --- Good signs ------------------------------------------------------------------------------


def _no_violations(f: Facts) -> str | None:
    open_now = f.violations.get("open_now", {})
    if open_now.get("C", 0):
        return None
    if not open_now.get("total"):
        return "No open HPD violations."
    return "No open class C (immediately hazardous) violations."


def _no_heat_complaints(f: Facts) -> str | None:
    seasons = f.complaints.get("heat_seasons") or []
    if seasons and not any(s.get("complaints") for s in seasons):
        return f"No heat/hot water complaints in the last {len(seasons)} winters."
    return None


def _quiet_neighbor(f: Facts) -> str | None:
    rated = [r for r in f.complaints.get("categories", []) if r.get("percentile_vs_nearby") is not None]
    if rated and all(r["percentile_vs_nearby"] < 50 for r in rated):
        return "Fewer 311 complaints per apartment than most nearby buildings."
    return None


def _good_landlord(f: Facts) -> str | None:
    rates = [p["times_citywide_rate"] for p in _portfolios(f) if p.get("times_citywide_rate") is not None]
    if rates and (worst := max(rates)) <= 0.5:
        return f"Landlord's buildings average {worst}x the citywide rate of hazardous violations."
    return None


def _no_serious_history(f: Facts) -> str | None:
    court = f.history.get("housing_court", {})
    serious = (
        f.history.get("vacate_orders", {}).get("in_effect"),
        court.get("harassment_findings"),
        court.get("court_appointed_administrator"),
    )
    return (
        None if any(serious) else "No vacate orders in effect, harassment findings or court-appointed administrators."
    )


def _no_bedbugs(f: Facts) -> str | None:
    reports = f.history.get("bedbug_reports", [])
    filed = [r for r in reports if r.get("filed")]
    if not filed or not filed[0].get("apartments") or filed[0].get("infested", 0) / filed[0]["apartments"] >= 0.05:
        return None
    if any(not r.get("filed") for r in reports) or any(r.get("infested", 0) for r in filed):
        return None
    return "No bedbugs reported in recent bedbug reports."


GOOD_SIGNS = [
    GoodSign("violations", _no_violations),
    GoodSign("complaints", _no_heat_complaints),
    GoodSign("complaints", _quiet_neighbor),
    GoodSign("landlord", _good_landlord),
    GoodSign("history", _no_serious_history),
    GoodSign("history", _no_bedbugs),
]


# --- Helpers ------------------------------------------------------------------------------------


def describe_points(rule: Rule) -> str:
    """The rule's weights in words, for the README: '30 at 50+ per 100 apartments, 20 at 20+, ...'."""
    text = _describe_tiers(rule.tiers, rule.unit, rule.threshold_format)
    if rule.count_tiers:
        joiner = "but no more than by count" if rule.combine == "lower" else "or by count"
        text += f"; {joiner}: {_describe_tiers(rule.count_tiers, '', '{:g}+')}"
    if rule.cap is not None:
        text += f"; {rule.cap} at most in total"
    return text


def _describe_tiers(tiers: Tiers, unit: str, threshold_format: str | Callable[[float], str]) -> str:
    if len(tiers) == 1 and tiers[0][0] == 1 and not unit:
        return str(tiers[0][1])
    parts = []
    for i, (at_least, points) in enumerate(tiers):
        if at_least <= 0:
            parts.append(f"otherwise {points}")
            continue
        threshold = threshold_format(at_least) if callable(threshold_format) else threshold_format.format(at_least)
        parts.append(f"{points} at {threshold}{f' {unit}' if unit and i == 0 else ''}")
    return ", ".join(parts)


def _portfolios(f: Facts) -> list[dict]:
    return [f.landlord[k] for k in ("portfolio", "owner_portfolio", "management_portfolio") if k in f.landlord]


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
