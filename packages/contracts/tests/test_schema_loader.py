"""Loader behavior: schema discovery, draft 2020-12 well-formedness, and validate()."""

import jsonschema
import pytest
from jsonschema import Draft202012Validator

from spykt_contracts import load_schema, schema_names, validate

EXPECTED_SCHEMAS = {
    "specialist_input",
    "specialist_output",
    "results/a1_zuzu_frame",
    "results/a2_genome",
    "results/a3_planner",
    "results/a8_sentinel",
    "results/a9_verifier",
}


def test_all_expected_schemas_present():
    assert set(schema_names()) == EXPECTED_SCHEMAS


@pytest.mark.parametrize("name", sorted(EXPECTED_SCHEMAS))
def test_schemas_are_valid_draft_2020_12(name: str):
    schema = load_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_result_schemas_loadable_by_short_name():
    assert load_schema("a2_genome") == load_schema("results/a2_genome")


def test_unknown_schema_name_raises_keyerror():
    with pytest.raises(KeyError, match="no_such_contract"):
        load_schema("no_such_contract")
    with pytest.raises(KeyError):
        validate("no_such_contract", {})


def test_load_schema_returns_fresh_copies():
    """Mutating a loaded schema must not poison the cached validator."""
    load_schema("specialist_input").clear()
    doc = {
        "job_id": "j1",
        "student_pseudonym": "student-aardvark",
        "task": "score",
        "context_refs": [],
        "budget_tokens": 1000,
        "autonomy_ceiling": "L0",
    }
    validate("specialist_input", doc)
    with pytest.raises(jsonschema.ValidationError):
        validate("specialist_input", {**doc, "budget_tokens": 0})
