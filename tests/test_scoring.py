from datetime import date

import pytest

from nyc_lease_lens import scoring
from nyc_lease_lens.scoring import engine

TODAY = date(2026, 9, 30)
BUILDING = {"building": {"residential_units_on_lot": 100}}  # 100 apartments, so counts are also per-100 rates


def grade(*, violations=None, complaints=None, landlord=None, history=None, building=BUILDING):
    return scoring.score(building, violations, complaints, landlord, history, today=TODAY)


def flags(**parts) -> dict[str, int]:
    return {f["finding"]: f["points"] for f in grade(**parts)["red_flags"]}


def points(**parts) -> list[int]:
    return [f["points"] for f in grade(**parts)["red_flags"]]


# --- Violations ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("open_c", "units", "expected"),
    [
        (1, 100, 5),  # 1 per 100 apartments
        (5, 100, 12),  # 5 per 100
        (20, 100, 20),  # 20 per 100
        (50, 100, 30),  # 50 per 100
        (1, 10, 5),  # 10 per 100, but a single violation is capped at 5
        (2, 10, 12),  # 20 per 100, capped at 12 by the count of 2
        (1234, 1208, 30),
        (3, None, 12),  # apartments unknown: the count alone decides
    ],
)
def test_class_c_points_are_the_lower_of_rate_and_count(open_c, units, expected):
    building = {"building": {"residential_units_on_lot": units}} if units else {}
    assert points(violations={"open_now": {"total": open_c, "C": open_c}}, building=building) == [expected]


@pytest.mark.parametrize(
    ("count", "units", "expected"),
    [(1, 100, 5), (5, 100, 10), (19, 1000, 5), (20, 1000, 10), (3, None, 5)],
)
def test_rent_impairing_points_are_the_higher_of_rate_and_count(count, units, expected):
    building = {"building": {"residential_units_on_lot": units}} if units else {}
    assert points(violations={"open_now": {"total": count}, "rent_impairing_open": count}, building=building) == [
        expected
    ]


@pytest.mark.parametrize(("days", "expected"), [(365, []), (366, [5]), (1824, [5]), (1825, [8])])
def test_oldest_open_hazard(days, expected):
    oldest = {"days_open": days, "description": "Repair the leak"}
    assert points(violations={"open_now": {"total": 1}, "oldest_open_hazardous": oldest}) == expected


def test_illegal_occupancy_and_lead_paint():
    categories = [
        {"category": "illegal_occupancy", "open": 2, "open_class_c": 0},
        {"category": "lead_paint", "open": 3, "open_class_c": 1},
    ]
    assert flags(violations={"open_now": {"total": 5}, "categories": categories}) == {
        "2 open illegal-occupancy violations: apartments may not be legal": 10,
        "1 open class C lead paint violations": 5,
    }


# --- 311 complaints ------------------------------------------------------------------------------


def neighbors(**percentiles):
    return {"categories": [{"category": c, "complaints": 1, "percentile_vs_nearby": p} for c, p in percentiles.items()]}


@pytest.mark.parametrize(("percentile", "expected"), [(79, []), (80, [3]), (94, [3]), (95, [6]), (100, [6])])
def test_habitability_complaints_compared_with_neighbors(percentile, expected):
    assert points(complaints=neighbors(mold=percentile)) == expected


def test_habitability_complaints_are_capped_at_18_in_total():
    assert points(complaints=neighbors(heat_hot_water=99, pests=99, mold=99, leaks_plumbing=99)) == [6, 6, 6]


def test_noise_counts_only_at_the_95th_percentile():
    assert points(complaints=neighbors(noise=94)) == []
    assert flags(complaints=neighbors(noise=100)) == {"More noise complaints per apartment than any nearby building": 3}


@pytest.mark.parametrize(("days", "expected"), [(19, []), (20, [6]), (59, [6]), (60, [12])])
def test_heat_days_in_a_recent_winter(days, expected):
    seasons = [{"complaints": days, "days_with_complaints": days}]
    assert points(complaints={"heat_seasons": seasons}) == expected


def test_only_the_last_two_winters_count_for_heat_days():
    seasons = [{"complaints": 90, "days_with_complaints": 90}, {"complaints": 0, "days_with_complaints": 0}] * 2
    seasons = seasons[:3]  # 90, 0, 90 -> the worse of the last two is 90
    assert flags(complaints={"heat_seasons": seasons})["Heat/hot water complaints on 90 days in a recent winter"] == 12


def test_repeated_bad_winters():
    seasons = [{"complaints": 30, "days_with_complaints": 25}] * 2 + [{"complaints": 0, "days_with_complaints": 0}]
    assert flags(complaints={"heat_seasons": seasons})["Heat problems in 2 of the last 3 winters"] == 4


# --- Landlord ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("ratio", "expected"), [(1.24, []), (1.25, [5]), (1.99, [5]), (2, [10]), (6.7, [10])])
def test_landlord_rate_compared_with_the_city(ratio, expected):
    assert points(landlord={"registration": {"status": "current"}, "portfolio": {"times_citywide_rate": ratio}}) == (
        expected
    )


def test_the_worse_of_owner_and_management_portfolios_counts():
    landlord = {
        "registration": {"status": "current"},
        "owner_portfolio": {"times_citywide_rate": 0.3},
        "management_portfolio": {"times_citywide_rate": 2.5, "buildings_in_enforcement_program": 4},
    }
    assert flags(landlord=landlord) == {
        "Landlord's buildings average 2.5x the citywide rate of hazardous violations": 10,
        "4 of the landlord's buildings are in the city's program for the worst-maintained buildings": 5,
    }


@pytest.mark.parametrize(("units", "expected"), [(2, []), (3, [5]), (None, [])])
def test_unregistered_buildings_only_count_with_three_or_more_apartments(units, expected):
    building = {"building": {"residential_units_on_lot": units}} if units else {}
    assert points(landlord={"registered": False}, building=building) == expected


def test_lapsed_registration():
    assert flags(landlord={"registration": {"status": "lapsed"}}) == {"HPD registration has lapsed": 5}
    assert flags(landlord={"registration": {"status": "renewal_not_yet_in_data"}}) == {}


# --- Tenant history -----------------------------------------------------------------------------


def court(**fields):
    return {"housing_court": fields}


@pytest.mark.parametrize(("scope", "expected"), [("Entire Building", 30), ("Partial", 20), (None, 20)])
def test_vacate_orders(scope, expected):
    order = {"scope": scope, "since": "2025-04-18", "reason": "Fire Damage", "address": "760 Eldert Lane"}
    assert points(history={"vacate_orders": {"in_effect": [order]}}) == [expected]


@pytest.mark.parametrize(
    ("decided", "expected"),
    [("2016-01-01", 25), ("2015-12-31", 10), (None, 10)],  # recent means since Jan 1, ten years back
)
def test_harassment_findings_count_less_after_ten_years(decided, expected):
    assert points(history=court(harassment_findings=[{"decided": decided}])) == [expected]


@pytest.mark.parametrize(("opened", "expected"), [(["2012-01-09"], 5), (["2012-01-09", "2020-03-01"], 15), ([], 5)])
def test_court_appointed_administrator(opened, expected):
    assert points(history=court(court_appointed_administrator={"cases": len(opened) or 1, "opened": opened})) == [
        expected
    ]


def test_false_repair_certifications():
    assert points(history=court(cases={"false_repair_certifications": 2})) == [8]


@pytest.mark.parametrize(
    ("evictions", "expected"),
    # 100 apartments over 5 years: 4 evictions = 0.8 a year per 100, 5 = 1, 10 = 2
    [(4, []), (5, [4]), (9, [4]), (10, [8])],
)
def test_evictions_per_100_apartments_a_year(evictions, expected):
    assert points(history={"counting_since": "2021-09-30", "evictions": {"total": evictions}}) == expected


def test_missing_bedbug_reports_and_infestation_share():
    reports = [
        {"period_start": "2024-11-01", "filed": True, "apartments": 100, "infested": 5},
        {"period_start": "2023-11-01", "filed": False},
    ]
    assert flags(history={"bedbug_reports": reports}) == {
        "Missing required bedbug report(s) for 2023-11-01": 4,
        "Bedbugs reported in 5% of apartments in the latest report": 5,
    }


# --- Grades, ordering and good signs -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, "A"), (9, "A"), (10, "B"), (24, "B"), (25, "C"), (44, "C"), (45, "D"), (69, "D"), (70, "F"), (100, "F")],
)
def test_grade_bands(total, expected):
    # One rule worth exactly `total` points, run through the real engine.
    rule = scoring.Rule("test", "test", "violations", lambda f: [scoring.Hit(1, "x")], tiers=((1, total),))
    facts = scoring.Facts(BUILDING, {"open_now": {}}, {}, {}, {}, units=100, today=TODAY)
    assert engine.grade(facts, [rule], [])["grade"] == expected


def test_grade_bands_cover_0_to_100():
    assert scoring.grade_bands() == [("A", 0, 9), ("B", 10, 24), ("C", 25, 44), ("D", 45, 69), ("F", 70, 100)]


def test_score_is_capped_at_100_and_flags_are_sorted_by_points():
    orders = [{"scope": "Entire Building", "since": "x", "reason": "y", "address": "z"}] * 4
    result = grade(history={"vacate_orders": {"in_effect": orders}, **court(cases={"false_repair_certifications": 1})})
    assert result["score"] == 100 and result["grade"] == "F"
    assert [f["points"] for f in result["red_flags"]] == [30, 30, 30, 30, 8]


def test_a_clean_building_gets_an_a_and_good_signs():
    result = grade(
        violations={"open_now": {"total": 0}},
        complaints={"heat_seasons": [{"complaints": 0, "days_with_complaints": 0}] * 3, **neighbors(mold=10)},
        landlord={"registration": {"status": "current"}, "portfolio": {"times_citywide_rate": 0.3}},
        history={"bedbug_reports": [{"period_start": "2024-11-01", "filed": True, "apartments": 10, "infested": 0}]},
    )
    assert (result["grade"], result["score"], result["red_flags"]) == ("A", 0, [])
    assert result["good_signs"] == [
        "No open HPD violations.",
        "No heat/hot water complaints in the last 3 winters.",
        "Fewer 311 complaints per apartment than most nearby buildings.",
        "Landlord's buildings average 0.3x the citywide rate of hazardous violations.",
        "No vacate orders in effect, harassment findings or court-appointed administrators.",
        "No bedbugs reported in recent bedbug reports.",
    ]


def test_missing_checks_add_no_points_and_no_good_signs():
    result = grade()
    assert (result["score"], result["red_flags"], result["good_signs"]) == (0, [], [])


# --- The rules table itself -----------------------------------------------------------------------


@pytest.mark.parametrize("rule", scoring.RULES, ids=[r.signal for r in scoring.RULES])
def test_rules_are_well_formed(rule):
    assert rule.signal and rule.source
    for tiers in (rule.tiers, rule.count_tiers):
        thresholds = [at_least for at_least, _ in tiers]
        assert thresholds == sorted(thresholds, reverse=True), "tiers must be highest first"
        assert all(p > 0 for _, p in tiers)


def test_describe_points_reads_as_plain_english():
    by_signal = {r.signal: scoring.describe_points(r) for r in scoring.RULES}
    assert by_signal["Open class C (immediately hazardous) violations"] == (
        "30 at 50+ per 100 apartments, 20 at 20+, 12 at 5+, otherwise 5; "
        "but no more than by count: 30 at 20+, 20 at 5+, 12 at 2+, 5 at 1+"
    )
    assert by_signal["Oldest open hazardous violation"] == "8 at 5+ years open, 5 at 1+ years"
    assert by_signal["Partial vacate order in effect"] == "20"
