import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

import requests

from nyc_lease_lens.data.client import OpenDataClient
from nyc_lease_lens.data.datasets import Dataset
from nyc_lease_lens.observability.log import ms_since

logger = logging.getLogger(__name__)
T = TypeVar("T")


class ToolError(Exception):
    """A failure the model should see and can explain to the user."""


class Tool(ABC):
    name: str
    description: str
    parameters: dict
    data_sources: tuple[Dataset, ...] = ()  # what the tool reads; the README data table uses this
    error_label = "Open Data lookup"  # failed requests become "<error_label> failed: <reason>"

    def __init__(self, client: OpenDataClient):
        self.client = client

    @property
    def schema(self) -> dict:
        """What the model sees."""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def sources(self) -> tuple[Dataset, ...]:
        """Every dataset a call to this tool can read, for the chat page's sources footer."""
        return self.data_sources

    def query(self, dataset: Dataset, params: dict) -> list[dict]:
        """Run a SoQL query. A failed request becomes a ToolError the model can explain."""
        return self.fetch(self.client.socrata, dataset, params)

    def query_in(self, dataset: Dataset, field: str, values: list[str], params: dict) -> list[dict]:
        """query() filtered to `field IN (values)`, for any number of values."""
        return self.fetch(self.client.socrata_in, dataset, field, values, params)

    def fetch(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Call any Open Data helper, turning a failed request into a ToolError."""
        try:
            return fn(*args, **kwargs)
        except requests.RequestException as e:
            raise ToolError(f"{self.error_label} failed: {e}") from e

    @staticmethod
    def validate_bbl(bbl: str) -> str:
        if not re.fullmatch(r"\d{10}", bbl):
            raise ToolError(f"'{bbl}' is not a 10-digit BBL. Call lookup_building first.")
        return bbl

    @staticmethod
    def validate_bin(bin: str) -> str:
        if not re.fullmatch(r"\d{7}", bin):
            raise ToolError(f"'{bin}' is not a 7-digit BIN. Call lookup_building first.")
        return bin


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {tool.name: tool for tool in tools}

    @property
    def schemas(self) -> list[dict]:
        return [tool.schema for tool in self._tools.values()]

    def sources_for(self, tool_names: Iterable[str]) -> list[Dataset]:
        """The datasets behind a set of tool calls, deduplicated, in the order the tools ran."""
        found: dict[str, Dataset] = {}
        for name in tool_names:
            if tool := self._tools.get(name):
                for dataset in tool.sources():
                    found.setdefault(dataset.id, dataset)
        return list(found.values())

    def run(self, name: str, args: dict) -> str:
        """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
        tool = self._tools.get(name)
        if tool is None:
            logger.warning("unknown tool requested", extra={"tool": name})
            return _error(f"Unknown tool '{name}'. Available: {list(self._tools)}")

        logger.debug("tool arguments", extra={"tool": name, "arguments": args})
        started = time.perf_counter()
        fields: dict[str, Any] = {"tool": name}
        try:
            result = json.dumps(tool.run(**args))
        except TypeError as e:
            logger.warning("bad tool arguments", extra=fields | {"error": str(e)})
            return _error(f"Bad arguments for {name}: {e}")
        except ToolError as e:
            logger.warning("tool error", extra=fields | {"error": str(e), "duration_ms": ms_since(started)})
            return _error(str(e))
        except Exception:
            logger.exception("tool crashed", extra=fields | {"duration_ms": ms_since(started)})
            raise
        logger.info("tool finished", extra=fields | {"duration_ms": ms_since(started), "result_chars": len(result)})
        return result


def _error(message: str) -> str:
    return json.dumps({"error": message})
