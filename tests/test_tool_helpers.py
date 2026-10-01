from datetime import UTC, date, datetime

import pytest
import time_machine

from nyc_lease_lens.tools import complaints, history, violations


@pytest.mark.parametrize(
    ("description", "category"),
    [
        ("§ 27-2017.4 ABATE THE INFESTATION CONSISTING OF MICE IN THE ENTIRE APARTMENT", "pests"),
        ("§ 27-2056.6 CORRECT THE LEAD-BASED PAINT HAZARD", "lead_paint"),
        ("TRACE AND REPAIR THE SOURCE AND ABATE THE VISIBLE MOLD CONDITION", "mold"),
        ("PROVIDE HOT WATER AT ALL HOT WATER FIXTURES", "heat_hot_water"),
        ("DISCONTINUE USE OF ROOMS FOR LIVING", "illegal_occupancy"),
        ("REPAIR THE SOURCE AND ABATE THE EVIDENCE OF A WATER LEAK", "leaks_plumbing"),
        ("PROPERLY REPAIR WITH SIMILAR MATERIAL THE BROKEN OR DEFECTIVE PLASTER", "other_repairs"),
        ("ABATE MOLD AND THE MICE", "mold"),  # first match wins: specific hazards before broad ones
    ],
)
def test_violation_categories(description, category):
    assert violations._categorize(description) == category


@pytest.mark.parametrize(
    ("description", "cleaned"),
    [
        (
            "§ 27-2045(B)(1)(B) HMC, § 12-06, § 12-07, § 12-09 RCNY "
            "REPAIR, REPLACE OR PROVIDE AN APPROVED SMOKE DETECTOR",
            "Repair, replace or provide an approved smoke detector",
        ),
        (
            "HMC ADM CODE: § 27-2017.4 ABATE THE INFESTATION CONSISTING OF MICE IN THE ENTIRE APARTMENT",
            "Abate the infestation consisting of mice in the entire apartment",
        ),
        (
            "567 (C) § 27-2017, 27-2017.1, 27-2019 HMC: ABATE THE NUISANCE CONSISTING OF ROACHES",
            "Abate the nuisance consisting of roaches",
        ),
        ("SECTION 27-2040 ADM CODE PROVIDE ADEQUATE LIGHTING", "Provide adequate lighting"),
    ],
)
def test_violation_descriptions_lose_their_legal_citation(description, cleaned):
    assert violations._clean(description) == cleaned


def test_long_violation_descriptions_are_shortened_at_a_word():
    cleaned = violations._clean("REPAIR " + "THE WALL " * 40)
    assert len(cleaned) <= 183 and cleaned.endswith("...") and not cleaned.endswith(" ...")


@pytest.mark.parametrize(
    ("complaint_type", "descriptor", "category"),
    [
        ("HEAT/HOT WATER", "ENTIRE BUILDING", "heat_hot_water"),
        ("UNSANITARY CONDITION", "PESTS", "pests"),
        ("Rodent", "Rat Sighting", "pests"),
        ("UNSANITARY CONDITION", "MOLD", "mold"),
        ("UNSANITARY CONDITION", "SEWAGE", "leaks_plumbing"),
        ("WATER LEAK", None, "leaks_plumbing"),
        ("Noise - Residential", "Banging/Pounding", "noise"),
        ("UNSANITARY CONDITION", "GARBAGE/RECYCLING STORAGE", "sanitation"),
        ("ELEVATOR", None, "repairs"),
        ("Illegal Parking", None, "other"),
    ],
)
def test_complaint_categories(complaint_type, descriptor, category):
    assert complaints._categorize(complaint_type, descriptor) == category


@pytest.mark.parametrize(
    ("day", "season_year"),
    [(date(2025, 10, 1), 2025), (date(2026, 5, 31), 2025), (date(2026, 9, 30), 2025), (date(2026, 1, 15), 2025)],
)
def test_heating_season_year(day, season_year):
    assert complaints._season_year(day) == season_year


def test_heat_complaints_are_grouped_by_season_and_summer_is_ignored():
    days = [
        {"day": "2025-12-01T00:00:00.000", "n": "5"},
        {"day": "2026-02-10T00:00:00.000", "n": "2"},
        {"day": "2026-07-04T00:00:00.000", "n": "9"},  # summer: not a heating-season complaint
        {"day": "2024-01-20T00:00:00.000", "n": "1"},
    ]
    with time_machine.travel(datetime(2026, 9, 30, 16, tzinfo=UTC), tick=False):
        seasons = complaints._heat_seasons(days)
    assert seasons == [
        {"season": "2023-24", "complaints": 1, "days_with_complaints": 1},
        {"season": "2024-25", "complaints": 0, "days_with_complaints": 0},
        {"season": "2025-26", "complaints": 7, "days_with_complaints": 2},
    ]


@pytest.mark.parametrize(
    ("today", "periods"),
    [
        # Reports for the period ending Oct 31 are due Dec 31, so the latest due period started two years back.
        (datetime(2026, 9, 30, 16, tzinfo=UTC), ["2024-11-01", "2023-11-01", "2022-11-01"]),
        (datetime(2027, 1, 2, 16, tzinfo=UTC), ["2025-11-01", "2024-11-01", "2023-11-01"]),
    ],
)
def test_due_bedbug_periods(today, periods):
    with time_machine.travel(today, tick=False):
        assert history._due_bedbug_periods() == periods
