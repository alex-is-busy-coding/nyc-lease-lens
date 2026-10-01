import gzip
import json
from datetime import date

import pytest

from nyc_lease_lens.data import datasets
from nyc_lease_lens.data.client import OpenDataClient
from nyc_lease_lens.data.recording import Recording, RecordingClient, ReplayClient, response_key


@pytest.fixture
def fake_http(monkeypatch):
    """Answer every HTTP request from the client with a canned body, and count the calls."""
    calls = []

    def get(self, source, url, params):
        calls.append((source, params))
        return {"features": [{"id": 1}]} if source == "geosearch" else [{"n": "5"}]

    monkeypatch.setattr(OpenDataClient, "_get", get)
    return calls


def test_recording_client_keeps_a_copy_of_each_response(fake_http):
    client = RecordingClient(geosearch_url="g", socrata_url="s/{dataset}", timeout=1)
    rows = client.socrata(datasets.HPD_VIOLATIONS, {"$where": "x"})
    rows[0]["_annotated"] = True  # tools add fields to the rows they get back
    client.geosearch("157 Ludlow St")
    assert client.recorded == {
        response_key("socrata", datasets.HPD_VIOLATIONS, {"$where": "x"}): [{"n": "5"}],
        response_key("geosearch", None, {"text": "157 Ludlow St", "size": 10}): [{"id": 1}],
    }


def test_recording_round_trips_through_the_fixture_format(tmp_path):
    path = tmp_path / "recording.json.gz"
    entry = {"key": ["socrata", "wvxf-dwi5", {"$where": "x"}], "response": [{"n": "5"}]}
    with gzip.open(path, "wt") as f:
        json.dump({"recorded_on": "2026-09-30", "responses": [entry]}, f)
    recording = Recording.load(path)
    assert recording.recorded_on == date(2026, 9, 30)
    assert ReplayClient(recording).socrata(datasets.HPD_VIOLATIONS, {"$where": "x"}) == [{"n": "5"}]


def test_replay_without_live_fails_on_unrecorded_queries():
    with pytest.raises(AssertionError, match="not recorded"):
        ReplayClient(Recording(date(2026, 9, 30), {})).socrata("abcd-1234", {})


def test_replay_falls_back_to_live_and_counts_it(fake_http):
    live = OpenDataClient("g", "s/{dataset}", timeout=1)
    client = ReplayClient(Recording(date(2026, 9, 30), {}), live=live)
    assert client.socrata("abcd-1234", {"q": 1}) == [{"n": "5"}]
    assert client.socrata("abcd-1234", {"q": 1}) == [{"n": "5"}]  # now served from the recording
    assert len(fake_http) == 1 and client.live_queries == [response_key("socrata", "abcd-1234", {"q": 1})]
