"""Regenerate the tools table in README.md from the Tool classes in nyc_lease_lens.tools.

Usage: uv run python scripts/update_readme_tools.py [--check]
"""

import argparse
import importlib
import inspect
import pkgutil
import re
import sys
from pathlib import Path

import nyc_lease_lens.tools as tools_package
from nyc_lease_lens.tools import build_registry
from nyc_lease_lens.tools.base import Tool

README = Path(__file__).resolve().parent.parent / "README.md"
START, END = "<!-- tools:start -->", "<!-- tools:end -->"


def discover_tools() -> list[type[Tool]]:
    for module in pkgutil.iter_modules(tools_package.__path__):
        importlib.import_module(f"{tools_package.__name__}.{module.name}")
    found, pending = [], list(Tool.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if not inspect.isabstract(cls):
            found.append(cls)
    return found


def render_table(tools: list[type[Tool]], registered: list[str]) -> str:
    order = {name: i for i, name in enumerate(registered)}
    tools = sorted(tools, key=lambda t: (order.get(t.name, len(order)), t.name))
    lines = ["| Tool | What it does | Parameters |", "| --- | --- | --- |"]
    for tool in tools:
        name = f"`{tool.name}`" + ("" if tool.name in order else " ⚠️ not registered")
        lines.append(f"| {name} | {_escape(_first_sentence(tool.description))} | {_parameters(tool.parameters)} |")
    return "\n".join(lines)


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

    readme = README.read_text()
    block = re.compile(re.escape(START) + ".*?" + re.escape(END), re.DOTALL)
    if not block.search(readme):
        print(f"README.md needs {START} and {END} markers where the table should go.", file=sys.stderr)
        return 1

    tools = discover_tools()
    registered = [schema["function"]["name"] for schema in build_registry(None).schemas]
    for tool in tools:
        if tool.name not in registered:
            print(f"warning: {tool.name} is defined but not registered in build_registry()", file=sys.stderr)

    updated = block.sub(lambda _: f"{START}\n{render_table(tools, registered)}\n{END}", readme)
    if updated == readme:
        print("README.md tools table is up to date.")
        return 0
    if args.check:
        print("README.md tools table is out of date. Run: make docs", file=sys.stderr)
        return 1
    README.write_text(updated)
    print(f"Updated README.md with {len(tools)} tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
