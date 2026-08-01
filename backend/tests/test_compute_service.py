"""
Dot notation for variables: a référentiel connection point (or any
hand-written structured `value`) stores its body as a JSON string — a
calculated column should be able to reach one field of it, not just the
whole blob.
"""
import json

import pandas as pd

from app.services.compute_service import ComputeService


def _frame():
    return pd.DataFrame({"nom": ["Alice", "Bob"]})


def test_a_json_variable_field_is_reachable_by_dot():
    variables = {"conn": json.dumps({"base_url": "https://x", "auth_header": "Authorization"})}
    val = ComputeService().evaluate(_frame(), "[conn.base_url]", variables=variables)
    assert list(val) == ["https://x"] * 2


def test_a_nested_field_is_reachable_by_chained_dots():
    variables = {"conn": json.dumps({"a": {"b": "deep"}})}
    val = ComputeService().evaluate(_frame(), "[conn.a.b]", variables=variables)
    assert list(val) == ["deep"] * 2


def test_a_missing_field_is_blank_not_an_error():
    variables = {"conn": json.dumps({"base_url": "https://x"})}
    val = ComputeService().evaluate(_frame(), "[conn.does_not_exist]", variables=variables)
    assert list(val) == [""] * 2


def test_a_non_json_variable_with_a_dotted_reference_is_blank():
    variables = {"plain": "just a string"}
    val = ComputeService().evaluate(_frame(), "[plain.field]", variables=variables)
    assert list(val) == [""] * 2


def test_plain_bracket_reference_is_unaffected():
    variables = {"societe": "italie"}
    val = ComputeService().evaluate(_frame(), "[societe]", variables=variables)
    assert list(val) == ["italie"] * 2
