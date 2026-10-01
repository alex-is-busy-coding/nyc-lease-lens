import gzip
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from nyc_lease_lens.data.client import OpenDataClient


def response_key(kind: str, dataset: Any, params: dict) -> str:
    """How a recorded response is looked up: the request, not the time it was made."""
    return json.dumps([kind, getattr(dataset, "id", dataset), params], sort_keys=True, default=str)


@dataclass(frozen=True)
class Recording:
    recorded_on: date
    responses: dict[str, str]  # key -> response as JSON text

    @classmethod
    def load(cls, path: Path) -> "Recording":
        with gzip.open(path, "rt") as f:
            data = json.load(f)
        return cls(
            recorded_on=date.fromisoformat(data["recorded_on"]),
            responses={response_key(*e["key"]): json.dumps(e["response"]) for e in data["responses"]},
        )


class RecordingClient(OpenDataClient):
    """Calls the live APIs and keeps a copy of every response, for scripts/record_opendata.py."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.recorded: dict[str, Any] = {}

    def geosearch(self, text: str, size: int = 10) -> list[dict]:
        response = super().geosearch(text, size)
        self.recorded[response_key("geosearch", None, {"text": text, "size": size})] = response
        return response

    def socrata(self, dataset: Any, params: dict) -> list[dict]:
        response = super().socrata(dataset, params)
        # Keep a copy: tools annotate the rows they get back.
        self.recorded[response_key("socrata", dataset, params)] = json.loads(json.dumps(response))
        return response


class ReplayClient(OpenDataClient):
    """Serves recorded responses instead of calling the APIs.

    A query that wasn't recorded fails, unless `live` is given: then it goes to the live API and is counted
    in `live_queries` (evals use this, since the model may phrase a request in a way that wasn't recorded).
    """

    def __init__(self, recording: Recording, live: OpenDataClient | None = None):
        super().__init__("https://geosearch.invalid", "https://socrata.invalid/{dataset}", timeout=1, retries=0)
        self.responses = dict(recording.responses)
        self.live = live
        self.live_queries: list[str] = []

    def geosearch(self, text: str, size: int = 10) -> list[dict]:
        key = response_key("geosearch", None, {"text": text, "size": size})
        return self._replay(key, lambda live: live.geosearch(text, size))

    def socrata(self, dataset: Any, params: dict) -> list[dict]:
        return self._replay(response_key("socrata", dataset, params), lambda live: live.socrata(dataset, params))

    def _replay(self, key: str, fetch: Any) -> Any:
        if key not in self.responses:
            if self.live is None:
                raise AssertionError(f"Open Data query was not recorded: {key[:300]}")
            self.live_queries.append(key)
            self.responses[key] = json.dumps(fetch(self.live))
        return json.loads(self.responses[key])  # a fresh copy each time: tools annotate rows in place
