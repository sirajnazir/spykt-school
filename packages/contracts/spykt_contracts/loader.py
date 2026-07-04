"""JSON Schema loader + validator for the specialist contracts (01-TECH_SPEC §5).

Schemas ship as package data under spykt_contracts/schemas/ (envelopes at the
top level, per-agent result schemas under results/), so they are present in
wheel installs, not just editable workspace installs. This module is the single
programmatic entry point: `load_schema(name)` and `validate(name, doc)`.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

# schemas/ is package data next to this module, so any on-disk install
# (editable workspace or built wheel) ships and resolves it identically.
SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

_SUFFIX = ".schema.json"


def schema_names() -> list[str]:
    """All loadable schema names, relative to schemas/ (e.g. 'specialist_input', 'results/a2_genome')."""
    return sorted(
        str(path.relative_to(SCHEMAS_DIR)).removesuffix(_SUFFIX) for path in SCHEMAS_DIR.rglob(f"*{_SUFFIX}")
    )


def _schema_path(name: str) -> Path:
    stem = name.removesuffix(_SUFFIX)
    candidates = (
        SCHEMAS_DIR / f"{stem}{_SUFFIX}",
        SCHEMAS_DIR / "results" / f"{stem}{_SUFFIX}",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise KeyError(f"Unknown contract schema {name!r}; available: {schema_names()}")


def load_schema(name: str) -> dict[str, Any]:
    """Load a schema by name ('specialist_input', 'a2_genome', or 'results/a2_genome').

    Returns a fresh dict each call so callers cannot corrupt the cached validator.
    """
    return json.loads(_schema_path(name).read_text())


@lru_cache(maxsize=None)
def _validator_for(resolved_path: Path) -> Draft202012Validator:
    schema = json.loads(resolved_path.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate(name: str, doc: Any) -> None:
    """Validate `doc` against the named schema; raises jsonschema.ValidationError on failure."""
    _validator_for(_schema_path(name)).validate(doc)
