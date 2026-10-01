import json
from types import SimpleNamespace

import pytest

from nyc_lease_lens.agent import loop
from nyc_lease_lens.agent.loop import Agent
from nyc_lease_lens.tools.base import Tool, ToolRegistry


class Lookup(Tool):
    name = "lookup_building"
    description = "Find a building. More detail."
    parameters = {"type": "object", "properties": {"address": {"type": "string"}}, "required": ["address"]}

    def run(self, address: str) -> dict:
        return {"bbl": "1004117502", "address": address}


def reply(content=None, tool_calls=None):
    """What litellm.completion returns, reduced to the parts the agent reads."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    message.model_dump = lambda: {"role": "assistant", "content": content, "tool_calls": tool_calls and len(tool_calls)}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    )


def tool_call(name, args, call_id="call_1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


@pytest.fixture
def model(monkeypatch):
    """A fake model: returns the queued replies in order (raising queued exceptions) and records every request."""
    fake = SimpleNamespace(replies=[], requests=[])

    def completion(**kwargs):
        fake.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        if isinstance(next_reply := fake.replies.pop(0), Exception):
            raise next_reply
        return next_reply

    monkeypatch.setattr(loop.litellm, "completion", completion)
    return fake


def make_agent(**kwargs):
    return Agent(tools=ToolRegistry([Lookup(client=None)]), model="test-model", **kwargs)


def test_runs_the_requested_tool_then_answers(model):
    model.replies = [
        reply(tool_calls=[tool_call("lookup_building", {"address": "157 Ludlow St"})]),
        reply(content="Grade B."),
    ]
    messages = [{"role": "system", "content": "prompt"}, {"role": "user", "content": "Check 157 Ludlow St"}]

    answer, calls = make_agent().run(messages)

    assert answer == "Grade B."
    assert calls == [
        {
            "name": "lookup_building",
            "args": {"address": "157 Ludlow St"},
            "result": json.dumps({"bbl": "1004117502", "address": "157 Ludlow St"}),
        }
    ]
    # The tool result goes back to the model on the next round, tied to its call.
    assert model.requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": calls[0]["result"],
    }
    # The conversation keeps every step.
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "assistant"]


def test_sends_the_configured_model_and_vertex_settings(model):
    model.replies = [reply(content="Hi.")]
    make_agent(vertex_project="proj", vertex_location="global").run([{"role": "user", "content": "hi"}])
    request = model.requests[0]
    assert (request["model"], request["vertex_project"], request["vertex_location"]) == ("test-model", "proj", "global")
    assert request["tools"][0]["function"]["name"] == "lookup_building"


def test_stops_after_the_round_limit(model):
    model.replies = [reply(tool_calls=[tool_call("lookup_building", {"address": "x"}, f"call_{i}")]) for i in range(3)]
    messages = [{"role": "user", "content": "loop forever"}]
    answer, calls = make_agent(max_tool_rounds=3).run(messages)
    assert answer == "Sorry, I hit my tool-call limit before finishing."
    assert len(calls) == 3 and len(model.requests) == 3
    # Every tool call keeps its result, and the history ends on an answer the next turn can follow.
    assert messages[-2]["role"] == "tool" and messages[-1] == {"role": "assistant", "content": answer}


def test_a_failure_mid_turn_leaves_the_history_unchanged(model):
    model.replies = [
        reply(tool_calls=[tool_call("lookup_building", {"address": "157 Ludlow St"})]),
        TimeoutError("model timed out"),
    ]
    messages = [{"role": "system", "content": "prompt"}, {"role": "user", "content": "Check 157 Ludlow St"}]
    with pytest.raises(TimeoutError):
        make_agent().run(messages)
    # No dangling tool call: the caller can retry or carry on.
    assert messages == [{"role": "system", "content": "prompt"}, {"role": "user", "content": "Check 157 Ludlow St"}]


@pytest.mark.parametrize("arguments", ['{"address": "157 Ludl', '["157 Ludlow St"]'])
def test_malformed_arguments_are_reported_back_to_the_model(model, arguments):
    call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="lookup_building", arguments=arguments))
    model.replies = [reply(tool_calls=[call]), reply(content="Let me try again.")]
    answer, calls = make_agent().run([{"role": "user", "content": "hi"}])
    assert answer == "Let me try again."
    assert calls[0]["args"] == {} and "error" in json.loads(calls[0]["result"])


def test_empty_arguments_mean_none_were_given(model):
    call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="lookup_building", arguments=""))
    model.replies = [reply(tool_calls=[call]), reply(content="Which address?")]
    _, calls = make_agent().run([{"role": "user", "content": "hi"}])
    assert calls[0]["args"] == {} and "Bad arguments" in json.loads(calls[0]["result"])["error"]


def test_unknown_tools_are_reported_back_to_the_model(model):
    model.replies = [reply(tool_calls=[tool_call("does_not_exist", {})]), reply(content="Sorry about that.")]
    answer, calls = make_agent().run([{"role": "user", "content": "hi"}])
    assert answer == "Sorry about that."
    assert json.loads(calls[0]["result"]) == {"error": "Unknown tool 'does_not_exist'. Available: ['lookup_building']"}
