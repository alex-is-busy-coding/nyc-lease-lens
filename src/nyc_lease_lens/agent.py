import json

import litellm

from nyc_lease_lens import config
from nyc_lease_lens.tools import ToolRegistry

SYSTEM_PROMPT = (
    "You are NYC Lease Lens. You help New York City renters check an apartment building "
    "for red flags before they sign a lease. When the user gives an address, call "
    "lookup_building first to identify the exact building. If the address is ambiguous, "
    "ask which borough they mean. Only state facts that come from tool results; never "
    "guess. Violation and complaint checks are not available yet: say so if asked."
)


class Agent:
    """Call the model, run the tools it asks for, repeat until it answers."""

    def __init__(
        self,
        tools: ToolRegistry,
        system_prompt: str = SYSTEM_PROMPT,
        model: str = config.MODEL,
        max_tool_rounds: int = 5,
    ):
        self.tools = tools
        self.system_prompt = system_prompt
        self.model = model
        self.max_tool_rounds = max_tool_rounds

    def run(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Complete until the model answers without asking for a tool.

        Returns the final text and a record of every tool call made along the way.
        """
        tool_calls = []

        for _ in range(self.max_tool_rounds):
            reply = litellm.completion(
                model=self.model,
                vertex_project=config.VERTEXAI_PROJECT,
                vertex_location=config.VERTEXAI_LOCATION,
                messages=messages,
                tools=self.tools.schemas,
            ).choices[0].message

            messages += [reply.model_dump()]

            if not reply.tool_calls:
                return reply.content, tool_calls

            for call in reply.tool_calls:
                args = json.loads(call.function.arguments)
                result = self.tools.run(call.function.name, args)
                tool_calls += [{"name": call.function.name, "args": args, "result": result}]

                messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

        return "Sorry, I hit my tool-call limit before finishing.", tool_calls
