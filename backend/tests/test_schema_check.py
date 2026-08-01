"""
`schema_check.validate_against_schema` — a declared schema is a contract:
a missing column or a value of the wrong declared type refuses the source
outright, never a silent partial accept.
"""
import pandas as pd
import pytest

from app.services.schema_check import validate_against_schema


def _df():
    return pd.DataFrame({"id": ["1", "2"], "nom": ["Alice", "Bob"], "age": ["17", "34"]})


def test_a_conforming_frame_passes():
    validate_against_schema(_df(), {"columns": ["id", "nom", "age"],
                                    "types": {"id": "integer", "nom": "string", "age": "integer"}})


def test_a_missing_column_is_refused_by_name():
    with pytest.raises(ValueError, match="pays"):
        validate_against_schema(_df(), {"columns": ["id", "pays"], "types": {}})


def test_a_wrong_type_is_refused_by_name():
    with pytest.raises(ValueError, match="nom"):
        validate_against_schema(_df(), {"columns": ["nom"], "types": {"nom": "integer"}})


def test_a_schema_with_no_columns_is_a_no_op():
    validate_against_schema(_df(), {"columns": [], "types": {}})


def test_string_type_never_fails():
    validate_against_schema(_df(), {"columns": ["nom"], "types": {"nom": "string"}})
