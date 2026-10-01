import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import litellm


@dataclass
class Turn:
    user: str
    answer: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class Transcript:
    turns: list[Turn]

    def turn(self, index: int) -> Turn:
        return self.turns[index]

    def evidence(self, upto: int) -> str:
        """Everything the agent was told up to and including a turn: user messages and tool results."""
        parts = []
        for turn in self.turns[: len(self.turns) + upto + 1 if upto < 0 else upto + 1]:
            parts.append(turn.user)
            parts += [call["result"] for call in turn.tool_calls]
        return "\n".join(parts)

    def as_text(self) -> str:
        lines = []
        for turn in self.turns:
            lines.append(f"USER: {turn.user}")
            for call in turn.tool_calls:
                lines.append(f"TOOL CALL: {call['name']}({json.dumps(call['args'])}) -> {call['result'][:1500]}")
            lines.append(f"ASSISTANT: {turn.answer}")
        return "\n".join(lines)


@dataclass
class Result:
    check: str
    passed: bool
    detail: str = ""


@dataclass
class Check:
    name: str
    run: Callable[[Transcript, "Judge"], Result]


Judge = Callable[[str, str], tuple[bool, str]]  # (transcript, criterion) -> (passed, reason)


# --- Tool use ------------------------------------------------------------------------------------


def calls(tool: str, turn: int = -1, **args: Any) -> Check:
    """The agent called `tool` in this turn, with arguments that include `args`."""

    def run(t: Transcript, _: Judge) -> Result:
        made = t.turn(turn).tool_calls
        names = [c["name"] for c in made]
        matching = [c for c in made if c["name"] == tool and all(c["args"].get(k) == v for k, v in args.items())]
        return Result(f"calls {tool}", bool(matching), f"called {names or 'no tools'}")

    return Check(f"calls {tool}", run)


def calls_no_tools(turn: int = -1) -> Check:
    def run(t: Transcript, _: Judge) -> Result:
        names = [c["name"] for c in t.turn(turn).tool_calls]
        return Result("calls no tools", not names, f"called {names}" if names else "")

    return Check("calls no tools", run)


def does_not_call(tool: str, turn: int = -1) -> Check:
    def run(t: Transcript, _: Judge) -> Result:
        names = [c["name"] for c in t.turn(turn).tool_calls]
        return Result(f"does not call {tool}", tool not in names, f"called {names}")

    return Check(f"does not call {tool}", run)


# --- Wording -----------------------------------------------------------------------------------------


def mentions(pattern: str, label: str, turn: int = -1) -> Check:
    """The answer matches a regular expression (case-insensitive)."""

    def run(t: Transcript, _: Judge) -> Result:
        found = re.search(pattern, t.turn(turn).answer, re.IGNORECASE)
        return Result(f"mentions {label}", bool(found), "" if found else f"no match for /{pattern}/")

    return Check(f"mentions {label}", run)


def avoids(pattern: str, label: str, turn: int = -1) -> Check:
    def run(t: Transcript, _: Judge) -> Result:
        found = re.search(pattern, t.turn(turn).answer, re.IGNORECASE)
        return Result(f"avoids {label}", not found, f"found {found.group(0)!r}" if found else "")

    return Check(f"avoids {label}", run)


# --- Grounding ----------------------------------------------------------------------------------------

# A number followed by a capital letter is a name, not a figure ("7A administrator").
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![A-Z\d])")


_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")  # "2. Open violations": numbering, not a figure


def _numbers(text: str, loose: bool = False) -> set[Decimal]:
    found = set()
    tokens = (
        re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")) if loose else _NUMBER.findall(_LIST_MARKER.sub(" ", text))
    )
    for token in tokens:
        try:
            found.add(Decimal(token.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return found


def grounded(turn: int = -1) -> Check:
    """Every number in the answer appears in what the agent was told (tool results and user messages).

    Catches invented figures and the model's own arithmetic, which tool results don't back up.
    """

    def run(t: Transcript, _: Judge) -> Result:
        # The data side matches any run of digits: figures also hide in keys like "issued_last_36_months".
        evidence = _numbers(t.evidence(turn), loose=True)
        missing = {n for n in _numbers(t.turn(turn).answer) - evidence if not _rounded_from(n, evidence)}
        shown = ", ".join(sorted(f"{n:f}" for n in missing))
        return Result("numbers are grounded", not missing, f"not in the data: {shown}" if missing else "")

    return Check("numbers are grounded", run)


def _rounded_from(number: Decimal, evidence: set[Decimal]) -> bool:
    """A round figure ("over 1,200") within 10% of a number in the data is a fair rounding, not an invention."""
    if number < 10 or number % 10:
        return False
    return any(abs(number - value) <= value / 10 for value in evidence if value > 0)


# --- Judgement ----------------------------------------------------------------------------------------


def judged(criterion: str, label: str) -> Check:
    """A yes/no question about the whole conversation, answered by a model."""

    def run(t: Transcript, judge: Judge) -> Result:
        passed, reason = judge(t.as_text(), criterion)
        return Result(f"judged: {label}", passed, reason)

    return Check(f"judged: {label}", run)


JUDGE_PROMPT = """You are grading one conversation between a renter and NYC Lease Lens, an assistant that \
checks New York City apartment buildings against public records.

Decide whether the conversation meets this criterion:
{criterion}

Judge only this criterion. Reply with JSON only: {{"pass": true or false, "reason": "one short sentence"}}"""


def model_judge(model: str, **completion_args: Any) -> Judge:
    def judge(transcript: str, criterion: str) -> tuple[bool, str]:
        reply = with_retries(
            litellm.completion,
            model=model,
            messages=[
                {"role": "system", "content": JUDGE_PROMPT.format(criterion=criterion)},
                {"role": "user", "content": transcript},
            ],
            response_format={"type": "json_object"},
            **completion_args,
        )
        text = reply.choices[0].message.content or ""
        try:
            verdict = json.loads(re.search(r"\{.*\}", text, re.S).group(0))  # type: ignore[union-attr]
            return bool(verdict["pass"]), str(verdict.get("reason", ""))
        except (AttributeError, KeyError, ValueError):
            return False, f"judge reply was not valid JSON: {text[:200]}"

    return judge


def with_retries(fn: Callable[..., Any], *args: Any, attempts: int = 4, **kwargs: Any) -> Any:
    """Retry model calls that were rate limited, so a busy API doesn't count against the agent."""
    for attempt in range(attempts):
        try:
            return fn(*args, **kwargs)
        except litellm.RateLimitError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * 2**attempt)
    raise AssertionError("unreachable")
