import json

import litellm

from nyc_lease_lens import config
from nyc_lease_lens.tools import TOOLS, run_tool

SYSTEM_PROMPT = (
    "You are a helpful assistant. When a question depends on the weather or "
    "outdoor conditions, call get_weather first, then answer in a sentence."
)
MAX_TOOL_ROUNDS = 5


def run_agent(messages: list[dict]) -> tuple[str, list[dict]]:
    """Complete until the model answers without asking for a tool.

    Returns the final text and a record of every tool call made along the way.
    """
    tool_calls = []

    for _ in range(MAX_TOOL_ROUNDS):
        reply = litellm.completion(
            model=config.MODEL,
            vertex_project=config.VERTEXAI_PROJECT,
            vertex_location=config.VERTEXAI_LOCATION,
            messages=messages,
            tools=TOOLS,
        ).choices[0].message

        messages += [reply.model_dump()]

        if not reply.tool_calls:
            return reply.content, tool_calls

        for call in reply.tool_calls:
            args = json.loads(call.function.arguments)
            result = run_tool(call.function.name, args)
            tool_calls += [{"name": call.function.name, "args": args, "result": result}]

            messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

    return "Sorry, I hit my tool-call limit before finishing.", tool_calls
