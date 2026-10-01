import pytest

from nyc_lease_lens.data.parsing import to_date, to_int
from nyc_lease_lens.data.soql import in_list, quote


@pytest.mark.parametrize(
    ("value", "expected"),
    [("abc", "'abc'"), ("O'Brien", "'O''Brien'"), ("", "''"), ("a''b", "'a''''b'")],
)
def test_quote_escapes_single_quotes(value, expected):
    assert quote(value) == expected


def test_in_list_quotes_text_and_leaves_numbers():
    assert in_list(["3042710001", "3042719001"]) == "('3042710001','3042719001')"
    assert in_list(["1", "2"], numeric=True) == "(1,2)"
    assert in_list(["After Trial", "After Inquest"]) == "('After Trial','After Inquest')"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12", 12), ("12.0", 12), ("1008350041.00000000", 1008350041), ("0", 0), (None, None), ("", None), ("n/a", None)],
)
def test_to_int(value, expected):
    assert to_int(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-28T00:00:00.000", "2026-09-28"),
        ("06/22/2026 00:00:00", "2026-06-22"),  # litigation finding dates
        ("2012-01-09", "2012-01-09"),
        (None, None),
        ("", None),
    ],
)
def test_to_date(value, expected):
    assert to_date(value) == expected
