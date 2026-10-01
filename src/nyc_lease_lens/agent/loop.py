import json
import logging
import time

import litellm

from nyc_lease_lens.agent.prompts import SYSTEM_PROMPT
from nyc_lease_lens.observability.log import ms_since
from nyc_lease_lens.tools import ToolRegistry

litellm.suppress_debug_info = True  # otherwise LiteLLM prints "Provider List" banners to stdout
logger = logging.getLogger(__name__)


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

        The turn's messages are added to `messages` only once it finishes. If anything raises partway, the
        history is left as it was: a tool call without its result would make the provider reject every later turn.
        Returns the final text and a record of every tool call made along the way.
        """
        turn: list[dict] = []
        tool_calls: list[dict] = []
        started = time.perf_counter()

        for round_number in range(1, self.max_tool_rounds + 1):
            call_started = time.perf_counter()
            completion = litellm.completion(
                model=self.model,
                vertex_project=self.vertex_project,
                vertex_location=self.vertex_location,
                messages=messages + turn,
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

            turn += [reply.model_dump()]

            if not reply.tool_calls:
                logger.info(
                    "agent answered",
                    extra={"rounds": round_number, "tools_run": len(tool_calls), "duration_ms": ms_since(started)},
                )
                messages += turn
                return reply.content, tool_calls

            for call in reply.tool_calls:
                args, result = self._run_tool(call.function.name, call.function.arguments)
                tool_calls += [{"name": call.function.name, "args": args, "result": result}]
                turn += [{"role": "tool", "tool_call_id": call.id, "content": result}]

        answer = "Sorry, I hit my tool-call limit before finishing."
        messages += [*turn, {"role": "assistant", "content": answer}]
        logger.warning(
            "tool round limit reached",
            extra={
                "max_tool_rounds": self.max_tool_rounds,
                "tools_run": len(tool_calls),
                "duration_ms": ms_since(started),
            },
        )
        return answer, tool_calls

    def _run_tool(self, name: str, raw_arguments: str | None) -> tuple[dict, str]:
        """Run one call; arguments that aren't a JSON object go back to the model as an error to fix."""
        try:
            args = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as e:
            logger.warning("malformed tool arguments", extra={"tool": name, "error": str(e)})
            return {}, json.dumps({"error": f"Arguments for {name} were not valid JSON: {e}"})
        if not isinstance(args, dict):
            return {}, json.dumps({"error": f"Arguments for {name} must be a JSON object"})
        return args, self.tools.run(name, args)
