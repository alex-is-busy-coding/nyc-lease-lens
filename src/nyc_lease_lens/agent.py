import json
import logging
import time

import litellm

from nyc_lease_lens.log import ms_since
from nyc_lease_lens.tools import ToolRegistry

litellm.suppress_debug_info = True  # otherwise LiteLLM prints "Provider List" banners to stdout
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are NYC Lease Lens. You help New York City renters check an apartment building "
    "for red flags before they sign a lease. When the user gives an address, call "
    "score_building_risk: it identifies the building, runs every check and grades it. If the "
    "address is ambiguous, ask which borough they mean. Start with the grade, then explain "
    "the red flags in order of points, then the good signs. Mention any data gaps. Use "
    "lookup_building, get_hpd_violations, get_311_complaints, get_landlord_profile and "
    "get_tenant_history only when the user asks for more detail. Describe landlords by what "
    "the public records show, not as accusations. Only state facts that come from tool "
    "results; never guess."
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
        tool_calls: list[dict] = []
        started = time.perf_counter()

        for round_number in range(1, self.max_tool_rounds + 1):
            call_started = time.perf_counter()
            completion = litellm.completion(
                model=self.model,
                vertex_project=self.vertex_project,
                vertex_location=self.vertex_location,
                messages=messages,
                tools=self.tools.schemas,
            )
            reply = completion.choices[0].message
            usage = getattr(completion, "usage", None)
            logger.info(
                "model call",
                extra={
                    "round": round_number,
                    "duration_ms": ms_since(call_started),
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "tool_calls": len(reply.tool_calls or []),
                },
            )

            messages += [reply.model_dump()]

            if not reply.tool_calls:
                logger.info(
                    "agent answered",
                    extra={"rounds": round_number, "tools_run": len(tool_calls), "duration_ms": ms_since(started)},
                )
                return reply.content, tool_calls

            for call in reply.tool_calls:
                args = json.loads(call.function.arguments)
                result = self.tools.run(call.function.name, args)
                tool_calls += [{"name": call.function.name, "args": args, "result": result}]

                messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

        logger.warning(
            "tool round limit reached",
            extra={
                "max_tool_rounds": self.max_tool_rounds,
                "tools_run": len(tool_calls),
                "duration_ms": ms_since(started),
            },
        )
        return "Sorry, I hit my tool-call limit before finishing.", tool_calls
