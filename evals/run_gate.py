"""Eval-gate CI job (01-TECH_SPEC §9).

Runs every pinned suite under evals/pinned/ and fails the build if any suite is
below its threshold. Suites are pinned at G3 (Verifier bootstrap is Phase 3 work,
GAP-12); until then this gate passes vacuously — by design, not omission.

Eval suites are L2 artifacts: this runner never mutates suites. Proposed suite
changes go to EVAL_CHANGES.md for human approval (CLAUDE.md prime directive 2).
"""

import sys
from pathlib import Path

PINNED_DIR = Path(__file__).resolve().parent / "pinned"


def main() -> int:
    suites = sorted(PINNED_DIR.glob("**/*.yaml")) if PINNED_DIR.exists() else []
    if not suites:
        print("eval-gate: no pinned suites (pre-G3). Gate passes vacuously.")
        return 0
    print(f"eval-gate: found {len(suites)} pinned suite(s) but no runner is implemented yet.")
    print("A pinned suite without a runner must fail loudly rather than pass silently.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
