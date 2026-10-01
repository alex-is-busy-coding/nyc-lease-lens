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


def grade(facts: Facts, rules: list[Rule], good_signs: list[GoodSign]) -> dict[str, Any]:
    """Apply the rules and good signs to the facts. Knows nothing about specific red flags."""
    flags = [finding for rule in rules if getattr(facts, rule.part) for finding in _apply(rule, facts)]
    good = [text for sign in good_signs if getattr(facts, sign.part) and (text := sign.find(facts))]

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


def units_for(building: dict[str, Any], complaints: dict[str, Any] | None) -> int | None:
    facts = building.get("building") or {}
    return (
        (complaints or {}).get("residential_units")
        or facts.get("residential_units_on_lot")
        or facts.get("apartments_in_building")
        or None
    )
