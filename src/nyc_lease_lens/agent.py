import json

import litellm

from nyc_lease_lens.tools import ToolRegistry

SYSTEM_PROMPT = (
    "You are NYC Lease Lens. You help New York City renters check an apartment building "
    "for red flags before they sign a lease. When the user gives an address, call "
    "lookup_building first to identify the exact building. If the address is ambiguous, "
    "ask which borough they mean. Then call get_hpd_violations with its bbl. Lead with "
    "the most serious findings: open class C, rent-impairing, and long-open violations. "
    "Only state facts that come from tool results; never guess. 311 complaint checks are "
    "not available yet: say so if asked."
)


class Agent:
    """Call the model, run the tools it asks for, repeat until it answers."""

    def __init__(
        self,
        tools: ToolRegistry,
        model: str,
        vertex_project: str | None = None,
        vertex_location: str | None = None,
        max_tool_rounds: int = 5,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        self.tools = tools
        self.model = model
        self.vertex_project = vertex_project
        self.vertex_location = vertex_location
        self.max_tool_rounds = max_tool_rounds
        self.system_prompt = system_prompt

    def run(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Complete until the model answers without asking for a tool.

        Returns the final text and a record of every tool call made along the way.
        """
        tool_calls = []

        for _ in range(self.max_tool_rounds):
            reply = litellm.completion(
                model=self.model,
                vertex_project=self.vertex_project,
                vertex_location=self.vertex_location,
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
