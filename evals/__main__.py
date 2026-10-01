"""Evaluate the agent: run each case in evals/cases.py with the real model and check its behavior.

Open Data comes from the tests' recorded responses (with the clock set to the recording day), falling back to the
live API for queries that weren't recorded, so results reflect the model rather than changing city data.

Usage: uv run python -m evals [--case NAME ...] [--repeat N]
Writes a JSON report to evals/results/.
"""

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Any

import time_machine

sys.path.insert(0, str(Path(__file__).parent))  # cases.py imports checks.py directly

from cases import CASES, Case  # noqa: E402
from checks import Check, Judge, Result, Transcript, Turn, model_judge, with_retries  # noqa: E402

from nyc_lease_lens.agent.loop import Agent  # noqa: E402
from nyc_lease_lens.agent.prompts import SYSTEM_PROMPT  # noqa: E402
from nyc_lease_lens.config import get_settings, use_vertex_project  # noqa: E402
from nyc_lease_lens.data.client import OpenDataClient  # noqa: E402
from nyc_lease_lens.data.recording import Recording, ReplayClient  # noqa: E402
from nyc_lease_lens.observability.log import configure_logging  # noqa: E402
from nyc_lease_lens.tools import build_registry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(__file__).parent / "results"
logger = logging.getLogger("evals")


def run_case(case: Case, agent: Agent) -> Transcript:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    transcript = Transcript(turns=[])
    for text in case.turns:
        turn = Turn(user=text)
        transcript.turns.append(turn)
        messages.append({"role": "user", "content": text})
        try:
            turn.answer, turn.tool_calls = with_retries(lambda: agent.run(messages))
        except Exception as e:  # a crash is a result to report, not a reason to stop the run
            turn.error = f"{type(e).__name__}: {str(e)[:300]}"
            break
    return transcript


def run_check(check: Check, transcript: Transcript, judge: Judge) -> Result:
    """A check that errors (e.g. the judge timed out) fails on its own instead of stopping the run."""
    try:
        return check.run(transcript, judge)
    except Exception as e:
        return Result(check.name, False, f"check could not run: {type(e).__name__}: {str(e)[:200]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", help="only run cases whose name contains this (repeatable)")
    parser.add_argument("--repeat", type=int, default=1, help="run each case this many times (the model varies)")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING", "text")
    logging.getLogger("evals").setLevel(logging.INFO)
    use_vertex_project(settings.llm)

    recording = Recording.load(ROOT / "tests" / "fixtures" / "opendata.json.gz")
    client = ReplayClient(recording, live=OpenDataClient.from_settings(settings.opendata))
    agent = Agent(
        tools=build_registry(client),
        model=settings.llm.model,
        vertex_project=settings.llm.vertexai_project,
        vertex_location=settings.llm.vertexai_location,
        max_tool_rounds=settings.llm.max_tool_rounds,
    )
    judge = model_judge(
        settings.llm.model,
        vertex_project=settings.llm.vertexai_project,
        vertex_location=settings.llm.vertexai_location,
    )
    cases = [c for c in CASES if not args.case or any(f.lower() in c.name.lower() for f in args.case)]

    report: list[dict[str, Any]] = []
    # The recorded responses were fetched on one day; tools build queries from today's date.
    recording_day = datetime.combine(recording.recorded_on, clock_time(12), tzinfo=UTC)
    with time_machine.travel(recording_day, tick=True):
        for case in cases:
            for attempt in range(1, args.repeat + 1):
                started, live_before = time.monotonic(), len(client.live_queries)
                transcript = run_case(case, agent)
                if error := next((t.error for t in transcript.turns if t.error), None):
                    results = [Result("ran without errors", False, error)]
                else:
                    results = [run_check(check, transcript, judge) for check in case.checks]
                seconds = time.monotonic() - started
                passed = all(r.passed for r in results)
                logger.info(
                    "%s  %s%s (%.0fs)",
                    "PASS" if passed else "FAIL",
                    case.name,
                    f" #{attempt}" if args.repeat > 1 else "",
                    seconds,
                )
                for r in results:
                    if not r.passed:
                        logger.info("        x %s: %s", r.check, r.detail)
                report.append(
                    {
                        "case": case.name,
                        "attempt": attempt,
                        "passed": passed,
                        "seconds": round(seconds, 1),
                        "live_queries": len(client.live_queries) - live_before,
                        "results": [r.__dict__ for r in results],
                        "transcript": [t.__dict__ for t in transcript.turns],
                    }
                )

    runs, passed_runs = len(report), sum(r["passed"] for r in report)
    checks = [c for r in report for c in r["results"]]
    logger.info(
        "\n%d/%d runs passed every check; %d/%d checks passed. Model: %s",
        passed_runs,
        runs,
        sum(c["passed"] for c in checks),
        len(checks),
        settings.llm.model,
    )
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y-%m-%d_%H%M%S}.json"
    out.write_text(json.dumps({"model": settings.llm.model, "system_prompt": SYSTEM_PROMPT, "runs": report}, indent=1))
    logger.info("Report: %s", out.relative_to(ROOT))
    return 0 if passed_runs == runs else 1


if __name__ == "__main__":
    sys.exit(main())
