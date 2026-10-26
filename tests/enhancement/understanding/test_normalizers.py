"""Tests for conservative, deterministic understanding normalizers."""

import pytest

from app.agent.enhancement.understanding.normalizers import (
    find_order_id_typo_candidates,
    normalize_explicit_date,
    normalize_integer,
    normalize_order_id,
    normalize_order_ids_in_text,
    normalize_positive_integer,
    normalize_stage,
    normalize_upper_token,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ORD057", "ORD-057"),
        ("ord-057", "ORD-057"),
        ("ORD 057", "ORD-057"),
        ("ORD_057", "ORD-057"),
        ("ORD57", "ORD-057"),
        (" ORD-7 ", "ORD-007"),
    ],
)
def test_normalize_order_id_accepts_safe_format_variants(raw, expected):
    assert normalize_order_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["ODR057", "OR057", "ORD-57A", "order 57", 57, None],
)
def test_normalize_order_id_does_not_guess_unsafe_values(raw):
    assert normalize_order_id(raw) is None


def test_normalize_order_ids_in_text_preserves_other_content():
    text = "Check ord057 and ORD 8, but do not alter ODR057."

    assert normalize_order_ids_in_text(text) == (
        "Check ORD-057 and ORD-008, but do not alter ODR057."
    )


def test_find_order_id_typo_candidates_requires_confirmation():
    assert find_order_id_typo_candidates("请检查 ODR057 和 odr_8") == [
        ("ODR057", "ORD-057"),
        ("odr_8", "ORD-008"),
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-04-25", "2026-04-25"),
        ("2026/4/25", "2026-04-25"),
        ("2026.04.05", "2026-04-05"),
        ("next Friday", None),
        ("2026-02-30", None),
    ],
)
def test_normalize_explicit_date_only_accepts_valid_numeric_dates(raw, expected):
    assert normalize_explicit_date(raw) == expected


def test_normalize_upper_token():
    assert normalize_upper_token(" assembly ") == "ASSEMBLY"
    assert normalize_upper_token("  ") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("assembly", "ASSEMBLY"),
        ("装配", "ASSEMBLY"),
        ("PACKAGING", "PACKING"),
        ("包装", "PACKING"),
        ("CUTTING", None),
    ],
)
def test_normalize_stage_uses_supported_canonical_values(raw, expected):
    assert normalize_stage(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(800, 800), (" 800 ", 800), (-5, -5), (True, None), (8.5, None)],
)
def test_normalize_integer(raw, expected):
    assert normalize_integer(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1, 1), ("800", 800), (0, None), (-5, None), (True, None)],
)
def test_normalize_positive_integer(raw, expected):
    assert normalize_positive_integer(raw) == expected
