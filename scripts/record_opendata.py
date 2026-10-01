"""Re-record the Open Data responses the tool tests replay (tests/fixtures/opendata.json.gz).

Runs every case in tests/test_tools_golden.py against the live APIs and saves each response, with the
date it was recorded on. Afterwards, update the golden outputs: UPDATE_GOLDEN=1 uv run pytest tests/test_tools_golden.py

Usage: uv run python scripts/record_opendata.py
"""

import gzip
import importlib.util
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

from nyc_lease_lens.config import get_settings  # noqa: E402
from nyc_lease_lens.data.recording import RecordingClient  # noqa: E402
from nyc_lease_lens.tools import build_registry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "opendata.json.gz"
logger = logging.getLogger("record_opendata")


def golden_cases() -> list[tuple[str, dict]]:
    spec = importlib.util.spec_from_file_location("test_tools_golden", ROOT / "tests" / "test_tools_golden.py")
    assert spec and spec.loader
    sys.path.insert(0, str(ROOT / "tests"))  # the test module imports conftest
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CASES


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    settings = get_settings().opendata
    client = RecordingClient(
        geosearch_url=settings.geosearch_url, socrata_url=settings.socrata_url, timeout=60, retries=3
    )
    recorded_on = date.today().isoformat()
    registry = build_registry(client)
    for tool, args in golden_cases():
        result = json.loads(registry.run(tool, args))
        logger.info("%s %s -> %s", tool, json.dumps(args), "error" if "error" in result else "ok")

    responses = [{"key": json.loads(k), "response": v} for k, v in sorted(client.recorded.items())]
    with gzip.open(FIXTURE, "wt", compresslevel=9) as f:
        json.dump({"recorded_on": recorded_on, "responses": responses}, f)
    logger.info("Recorded %d responses on %s to %s", len(responses), recorded_on, FIXTURE)
    logger.info("Next: UPDATE_GOLDEN=1 uv run pytest tests/test_tools_golden.py, then review the diff.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
