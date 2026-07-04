"""Specialist I/O contracts (01-TECH_SPEC §5).

Phase 1 generates JSON Schema files into packages/contracts/schemas/ (SpecialistInput,
SpecialistOutput, and per-agent result schemas) plus pydantic/zod mirrors, with
round-trip property tests at the G1 gate. Phase 0 reserves the package.
"""

from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
