from collections.abc import Iterable

# Small builders for Socrata's SoQL, so no tool hand-writes quoting.


def quote(value: str) -> str:
    """A SoQL string literal: 'O''Brien'."""
    return "'" + value.replace("'", "''") + "'"


def in_list(values: Iterable[str], *, numeric: bool = False) -> str:
    """`('a','b')` for text columns, or `(1,2)` for numeric ones such as PLUTO's bbl."""
    return "(" + ",".join(values if numeric else map(quote, values)) + ")"
