import os

# LiteLLM loads the developer's .env into the environment when imported; keep tests independent of it.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

import gzip  # noqa: E402
import json  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from datetime import UTC, date, datetime, time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
import time_machine  # noqa: E402

from nyc_lease_lens.data.client import OpenDataClient  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "golden_tools.json"
RECORDING = FIXTURES / "opendata.json.gz"  # written by scripts/record_opendata.py


def response_key(kind: str, dataset: Any, params: dict) -> str:
    return json.dumps([kind, getattr(dataset, "id", dataset), params], sort_keys=True, default=str)


class ReplayClient(OpenDataClient):
    """An OpenDataClient that serves recorded responses. Any query that wasn't recorded fails the test."""

    def __init__(self, responses: dict[str, str]):
        super().__init__("https://geosearch.invalid", "https://socrata.invalid/{dataset}", timeout=1, retries=0)
        self.responses = responses

    def geosearch(self, text: str, size: int = 10) -> list[dict]:
        return self._replay(response_key("geosearch", None, {"text": text, "size": size}))

    def socrata(self, dataset: Any, params: dict) -> list[dict]:
        return self._replay(response_key("socrata", dataset, params))

    def _replay(self, key: str) -> Any:
        if key not in self.responses:
            raise AssertionError(f"Open Data query was not recorded: {key[:300]}")
        return json.loads(self.responses[key])  # a fresh copy each time: tools annotate rows in place


@pytest.fixture(scope="session")
def recording() -> dict[str, Any]:
    with gzip.open(RECORDING, "rt") as f:
        return json.load(f)


@pytest.fixture
def replay_client(recording: dict[str, Any]) -> ReplayClient:
    return ReplayClient({response_key(*e["key"]): json.dumps(e["response"]) for e in recording["responses"]})


@pytest.fixture
def on_recording_day(recording: dict[str, Any]) -> Iterator[None]:
    """Tools build their queries from today's date, so replayed tests run as if it were the recording day.

    Noon UTC is the same calendar day from UTC-11 to UTC+11, so CI (UTC) and local runs agree.
    """
    noon = datetime.combine(date.fromisoformat(recording["recorded_on"]), time(12), tzinfo=UTC)
    with time_machine.travel(noon, tick=False):
        yield


# Golden outputs: `UPDATE_GOLDEN=1 uv run pytest` rewrites tests/fixtures/golden_tools.json from the current code.
UPDATING_GOLDEN = os.environ.get("UPDATE_GOLDEN") == "1"
_golden: dict[str, Any] = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


@pytest.fixture
def golden() -> dict[str, Any]:
    return _golden


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if UPDATING_GOLDEN:
        GOLDEN.write_text(json.dumps(_golden, indent=1, sort_keys=True) + "\n")
