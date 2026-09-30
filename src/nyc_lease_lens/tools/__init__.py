"""The tools the harness can run, and the JSON that describes them to the model.

To add a tool: write a module with the function and its SCHEMA, then register both below.
"""

import json

from nyc_lease_lens.tools import weather

# What the model sees.
TOOLS = [weather.SCHEMA]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {"get_weather": weather.get_weather}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
