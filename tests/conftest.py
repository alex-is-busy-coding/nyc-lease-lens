import os

# LiteLLM loads the developer's .env into the environment when imported; keep tests independent of it.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

import json  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from datetime import UTC, datetime, time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
import time_machine  # noqa: E402

from nyc_lease_lens.data.recording import Recording, ReplayClient  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "golden_tools.json"
RECORDING = FIXTURES / "opendata.json.gz"  # written by scripts/record_opendata.py


@pytest.fixture(scope="session")
def recording() -> Recording:
    return Recording.load(RECORDING)


@pytest.fixture
def replay_client(recording: Recording) -> ReplayClient:
    return ReplayClient(recording)


@pytest.fixture
def on_recording_day(recording: Recording) -> Iterator[None]:
    """Tools build their queries from today's date, so replayed tests run as if it were the recording day.

    Noon UTC is the same calendar day from UTC-11 to UTC+11, so CI (UTC) and local runs agree.
    """
    noon = datetime.combine(recording.recorded_on, time(12), tzinfo=UTC)
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
