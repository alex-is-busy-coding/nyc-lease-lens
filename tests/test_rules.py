import pytest

from nyc_lease_lens import rules
from nyc_lease_lens.rules import Range


def test_range_clamps_to_its_limits():
    months = Range(default=36, minimum=1, maximum=120)
    assert [months.clamp(v) for v in (0, 1, 36, 120, 500, -5)] == [1, 1, 36, 120, 120, 1]
    assert months.clamp("12") == 12  # models sometimes send numbers as strings


def test_range_schema_matches_its_limits():
    schema = Range(default=150, minimum=50, maximum=500).schema("Radius in meters.")
    assert schema == {
        "type": "integer",
        "minimum": 50,
        "maximum": 500,
        "description": "Radius in meters. Default 150.",
    }


@pytest.mark.parametrize("name", [n for n in dir(rules) if isinstance(getattr(rules, n), Range)])
def test_every_range_default_is_within_its_limits(name):
    r = getattr(rules, name)
    assert r.minimum <= r.default <= r.maximum


def test_heating_season_is_october_to_may():
    assert (rules.HEATING_SEASON_FIRST_MONTH, rules.HEATING_SEASON_LAST_MONTH) == (10, 5)
