"""Regenerate the generated tables in README.md (make targets, agent tools).

Each table lives between <!-- NAME:start --> and <!-- NAME:end --> markers.
Usage: uv run python scripts/update_readme.py [--check]
"""

import argparse
import importlib
import inspect
import pkgutil
import re
import sys
from pathlib import Path

import nyc_lease_lens.tools as tools_package
from nyc_lease_lens.tools import TOOL_CLASSES
from nyc_lease_lens.tools.base import Tool

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
MAKEFILE = ROOT / "makefile"


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
            print(f"warning: {tool.name} is defined but not listed in TOOL_CLASSES", file=sys.stderr)

    order = {name: i for i, name in enumerate(registered)}
    tools = sorted(tools, key=lambda t: (order.get(t.name, len(order)), t.name))
    lines = ["| Tool | What it does | Parameters |", "| --- | --- | --- |"]
    for tool in tools:
        name = f"`{tool.name}`" + ("" if tool.name in order else " ⚠️ not registered")
        lines.append(f"| {name} | {_escape(_first_sentence(tool.description))} | {_parameters(tool.parameters)} |")
    return "\n".join(lines)


SECTIONS = {"make": make_targets_table, "tools": tools_table}


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

    original = updated = README.read_text()
    for name, render in SECTIONS.items():
        start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
        block = re.compile(re.escape(start) + ".*?" + re.escape(end), re.DOTALL)
        match = block.search(updated)
        if not match:
            print(f"README.md needs {start} and {end} markers where the {name} table should go.", file=sys.stderr)
            return 1
        updated = f"{updated[: match.start()]}{start}\n{render()}\n{end}{updated[match.end() :]}"

    if updated == original:
        print("README.md tables are up to date.")
        return 0
    if args.check:
        print("README.md tables are out of date. Run: make docs", file=sys.stderr)
        return 1
    README.write_text(updated)
    print(f"Updated README.md ({', '.join(SECTIONS)} tables).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
