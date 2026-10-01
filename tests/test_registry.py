import json

import pytest

from nyc_lease_lens.data import datasets
from nyc_lease_lens.tools import build_registry
from nyc_lease_lens.tools.base import Tool, ToolError, ToolRegistry


class Echo(Tool):
    name = "echo"
    description = "Echo a word. Then more detail."
    parameters = {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]}

    def run(self, word: str) -> dict:
        if word == "fail":
            raise ToolError("the service said no")
        if word == "crash":
            raise KeyError("bug")
        return {"word": word}


@pytest.fixture
def registry():
    return ToolRegistry([Echo(client=None)])


def test_runs_a_tool_and_returns_json(registry):
    assert json.loads(registry.run("echo", {"word": "hi"})) == {"word": "hi"}


def test_unknown_tool_is_an_error_the_model_can_read(registry):
    assert json.loads(registry.run("nope", {})) == {"error": "Unknown tool 'nope'. Available: ['echo']"}


def test_bad_arguments_are_an_error_the_model_can_read(registry):
    result = json.loads(registry.run("echo", {"wrod": "hi"}))
    assert result["error"].startswith("Bad arguments for echo:")


def test_tool_errors_reach_the_model(registry):
    assert json.loads(registry.run("echo", {"word": "fail"})) == {"error": "the service said no"}


def test_bugs_are_not_hidden_from_the_caller(registry):
    with pytest.raises(KeyError):
        registry.run("echo", {"word": "crash"})


def test_schema_is_what_the_model_sees(registry):
    assert registry.schemas == [
        {
            "type": "function",
            "function": {"name": "echo", "description": Echo.description, "parameters": Echo.parameters},
        }
    ]


def test_sources_cover_the_tools_that_ran_without_duplicates():
    registry = build_registry(None)
    assert registry.sources_for([]) == registry.sources_for(["unknown_tool"]) == []
    every = registry.sources_for(["score_building_risk"])
    assert every == list(datasets.ALL)  # the full check reads every registered dataset
    assert registry.sources_for(["get_hpd_violations", "score_building_risk"])[0] == datasets.HPD_VIOLATIONS
    assert len(registry.sources_for(["get_hpd_violations", "score_building_risk"])) == len(every)


@pytest.mark.parametrize(("value", "ok"), [("1004117502", True), ("100411750", False), ("abc", False)])
def test_bbl_validation(value, ok):
    if ok:
        assert Tool.validate_bbl(value) == value
    else:
        with pytest.raises(ToolError, match="10-digit BBL"):
            Tool.validate_bbl(value)


@pytest.mark.parametrize(("value", "ok"), [("1005353", True), ("12", False), ("10053531", False)])
def test_bin_validation(value, ok):
    if ok:
        assert Tool.validate_bin(value) == value
    else:
        with pytest.raises(ToolError, match="7-digit BIN"):
            Tool.validate_bin(value)
