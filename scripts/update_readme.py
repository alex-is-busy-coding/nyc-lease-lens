"""Regenerate the generated sections of README.md (agent flow, grades, make targets, tools).

Each section lives between <!-- NAME:start --> and <!-- NAME:end --> markers.
Usage: uv run python scripts/update_readme.py [--check]
"""

import argparse
import importlib
import inspect
import logging
import pkgutil
import re
import sys
from pathlib import Path

import nyc_lease_lens.tools as tools_package
from nyc_lease_lens import scoring
from nyc_lease_lens.tools import TOOL_CLASSES
from nyc_lease_lens.tools.base import Tool
from nyc_lease_lens.tools.risk import ScoreBuildingRisk

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
MAKEFILE = ROOT / "makefile"
logger = logging.getLogger("update_readme")


def make_targets_table() -> str:
    """Documented targets (`target: deps ## description`), in makefile order, as `make help` shows them."""
    lines = ["| Target | What it does | Runs first |", "| --- | --- | --- |"]
    for match in re.finditer(r"^([a-zA-Z_-]+):([^#\n]*)## (.+)$", MAKEFILE.read_text(), re.MULTILINE):
        target, deps, description = match.group(1), match.group(2).split(), match.group(3).strip()
        runs_first = ", ".join(f"`{d}`" for d in deps) or "—"
        lines.append(f"| `make {target}` | {_escape(description)} | {runs_first} |")
    return "\n".join(lines)


def tools_table() -> str:
    tools = _discover_tools()
    registered = [cls.name for cls in TOOL_CLASSES]
    for tool in tools:
        if tool.name not in registered:
            logger.warning("%s is defined but not listed in TOOL_CLASSES", tool.name)

    order = {name: i for i, name in enumerate(registered)}
    tools = sorted(tools, key=lambda t: (order.get(t.name, len(order)), t.name))
    lines = ["| Tool | What it does | Parameters |", "| --- | --- | --- |"]
    for tool in tools:
        name = f"`{tool.name}`" + ("" if tool.name in order else " ⚠️ not registered")
        lines.append(f"| {name} | {_escape(_first_sentence(tool.description))} | {_parameters(tool.parameters)} |")
    return "\n".join(lines)


def flow_diagram() -> str:
    """Mermaid flowchart of one address check, built from ScoreBuildingRisk's lookup and checks."""
    risk = ScoreBuildingRisk
    lines = [
        "```mermaid",
        "flowchart TD",
        '    user(["Renter asks about an address"]) --> agent["Agent (LLM)"]',
        f'    agent -->|"one tool call"| risk["{risk.name}"]',
        f'    risk --> lookup["{risk.lookup_tool.name}<br/>address → BBL, BIN, location"]',
        '    lookup -.->|"ambiguous address: ask for the borough"| agent',
        '    lookup --> parallel{{"run checks in parallel"}}',
    ]
    for key, (tool, _) in risk.checks.items():
        lines.append(f'    parallel --> {key}["{tool.name}"]')
    lines.append(f"    {' & '.join(risk.checks)} --> scoring")
    lines += [
        '    scoring["scoring.py<br/>points → 0-100 score → grade A-F"]',
        '    scoring --> report["grade, score, red flags, good signs, data gaps"]',
        "    report --> agent",
        '    agent --> answer(["Answer: grade, then red flags by weight"])',
    ]
    for i, key in enumerate(risk.checks):
        label = '|"follow-up questions"|' if i == 0 else ""
        lines.append(f"    agent -.->{label} {key}")
    lines.append("```")
    return "\n".join(lines)


def grades_table() -> str:
    lines = ["| Grade | Score |", "| --- | --- |"]
    lines += [f"| {grade} | {low}–{high} |" for grade, low, high in scoring.grade_bands()]
    return "\n".join(lines)


SECTIONS = {"flow": flow_diagram, "grades": grades_table, "make": make_targets_table, "tools": tools_table}


def _discover_tools() -> list[type[Tool]]:
    for module in pkgutil.iter_modules(tools_package.__path__):
        importlib.import_module(f"{tools_package.__name__}.{module.name}")
    found, pending = [], list(Tool.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if not inspect.isabstract(cls):
            found.append(cls)
    return found


def _parameters(schema: dict) -> str:
    required = set(schema.get("required", []))
    rows = []
    for name, spec in schema.get("properties", {}).items():
        kind = spec.get("type", "")
        if kind == "array":
            kind = f"{spec.get('items', {}).get('type', '')}[]"
        details = [kind, "required" if name in required else "optional"]
        if "minimum" in spec or "maximum" in spec:
            details.append(f"{spec.get('minimum', '')}–{spec.get('maximum', '')}")
        text = f"`{name}` ({', '.join(details)}): {spec.get('description', '')}"
        if choices := spec.get("enum") or spec.get("items", {}).get("enum"):
            text += f" One of: {', '.join(f'`{c}`' for c in choices)}."
        rows.append(_escape(text))
    return "<br>".join(rows) or "—"


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if README.md is out of date, without writing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)

    original = updated = README.read_text()
    for name, render in SECTIONS.items():
        start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
        block = re.compile(re.escape(start) + ".*?" + re.escape(end), re.DOTALL)
        match = block.search(updated)
        if not match:
            logger.error("README.md needs %s and %s markers where the %s table should go.", start, end, name)
            return 1
        updated = f"{updated[: match.start()]}{start}\n{render()}\n{end}{updated[match.end() :]}"

    if updated == original:
        logger.info("README.md tables are up to date.")
        return 0
    if args.check:
        logger.error("README.md tables are out of date. Run: make docs")
        return 1
    README.write_text(updated)
    logger.info("Updated README.md (%s tables).", ", ".join(SECTIONS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
