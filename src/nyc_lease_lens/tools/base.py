import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from nyc_lease_lens.datasets import Dataset
from nyc_lease_lens.log import ms_since
from nyc_lease_lens.opendata import OpenDataClient

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """A failure the model should see and can explain to the user."""


class Tool(ABC):
    name: str
    description: str
    parameters: dict
    data_sources: tuple[Dataset, ...] = ()  # what the tool reads; the README data table uses this

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


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {tool.name: tool for tool in tools}

    @property
    def schemas(self) -> list[dict]:
        return [tool.schema for tool in self._tools.values()]

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
