import json
from abc import ABC, abstractmethod


class ToolError(Exception):
    """A failure the model should see and can explain to the user."""


class Tool(ABC):
    name: str
    description: str
    parameters: dict

    @property
    def schema(self) -> dict:
        """What the model sees."""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    @abstractmethod
    def run(self, **kwargs) -> dict: ...


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
            return _error(f"Unknown tool '{name}'. Available: {list(self._tools)}")
        try:
            return json.dumps(tool.run(**args))
        except TypeError as e:
            return _error(f"Bad arguments for {name}: {e}")
        except ToolError as e:
            return _error(str(e))


def _error(message: str) -> str:
    return json.dumps({"error": message})
