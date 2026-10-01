import json

import pytest
from conftest import UPDATING_GOLDEN

from nyc_lease_lens.tools import build_registry

# Each tool on buildings chosen for their edge cases. Responses come from the recorded fixture, so
# these run offline and catch any change to a tool's output or to the queries it sends.
CASES = [
    ("lookup_building", {"address": "157 Ludlow St", "borough": "Manhattan"}),  # condo
    ("lookup_building", {"address": "760 Eldert Lane", "borough": "Brooklyn"}),  # renumbered lot
    ("lookup_building", {"address": "350 5th Ave"}),  # in two boroughs
    ("lookup_building", {"address": "350 5th Ave", "borough": "Manhattan"}),  # not residential
    ("lookup_building", {"address": "asdkjh nowhere"}),
    ("get_hpd_violations", {"bbl": "1004117502"}),
    ("get_hpd_violations", {"bbl": "3042710001"}),  # over the sample cap
    ("get_hpd_violations", {"bbl": "3042710001", "months": 12}),
    ("get_hpd_violations", {"bbl": "1008350041"}),  # none
    ("get_hpd_violations", {"bbl": "nope"}),
    ("get_311_complaints", {"bbl": "1004117502", "latitude": 40.721016, "longitude": -73.98813}),
    ("get_311_complaints", {"bbl": "3042710001", "latitude": 40.671582, "longitude": -73.863669}),
    ("get_311_complaints", {"bbl": "3042710001", "categories": ["heat_hot_water", "pests"]}),
    (
        "get_311_complaints",
        {"bbl": "3042710001", "latitude": 40.671582, "longitude": -73.863669, "radius_m": 999, "months": 6},
    ),
    ("get_landlord_profile", {"bin": "3343250"}),  # big portfolio, above the citywide rate
    ("get_landlord_profile", {"bin": "1005353"}),  # well-kept portfolio
    ("get_landlord_profile", {"bin": "1015862"}),  # not registered
    ("get_landlord_profile", {"bin": "12"}),
    ("get_tenant_history", {"bbl": "3042710001"}),  # vacate order, missing bedbug report
    ("get_tenant_history", {"bbl": "1004117502"}),
    ("get_tenant_history", {"bbl": "3017920020"}),  # harassment finding
    ("get_tenant_history", {"bbl": "3023280003", "years": 10}),  # court-appointed administrator
    ("get_tenant_history", {"bbl": "1008350041"}),
    ("score_building_risk", {"address": "157 Ludlow St", "borough": "Manhattan"}),
    ("score_building_risk", {"address": "760 Eldert Lane", "borough": "Brooklyn"}),
    ("score_building_risk", {"address": "790 Lafayette Avenue", "borough": "Brooklyn"}),
    ("score_building_risk", {"address": "193 Bedford Avenue", "borough": "Brooklyn"}),
    ("score_building_risk", {"address": "350 5th Ave"}),
    ("score_building_risk", {"address": "350 5th Ave", "borough": "Manhattan"}),
]


def case_id(tool: str, args: dict) -> str:
    return f"{tool} {json.dumps(args, sort_keys=True)}"


@pytest.mark.parametrize(("tool", "args"), CASES, ids=[case_id(*c) for c in CASES])
def test_tool_output_matches_golden(tool, args, replay_client, on_recording_day, golden):
    output = json.loads(build_registry(replay_client).run(tool, args))
    key = case_id(tool, args)
    if UPDATING_GOLDEN:
        golden[key] = output
    assert key in golden, "No golden output yet: run `UPDATE_GOLDEN=1 uv run pytest`"
    assert output == golden[key]


def test_expected_grades(replay_client, on_recording_day):
    """The grades the README and CONTRIBUTING use as examples."""
    registry = build_registry(replay_client)
    grades = {
        address: json.loads(registry.run("score_building_risk", {"address": address, "borough": borough}))["grade"]
        for address, borough in [
            ("157 Ludlow St", "Manhattan"),
            ("760 Eldert Lane", "Brooklyn"),
            ("790 Lafayette Avenue", "Brooklyn"),
            ("193 Bedford Avenue", "Brooklyn"),
        ]
    }
    assert grades == {
        "157 Ludlow St": "B",
        "760 Eldert Lane": "F",
        "790 Lafayette Avenue": "F",
        "193 Bedford Avenue": "A",
    }
