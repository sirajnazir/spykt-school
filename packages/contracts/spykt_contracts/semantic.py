"""Semantic contract checks that JSON Schema cannot express (01-TECH_SPEC §5).

A3 Planner rule: ≥60% of a week plan's tasks must reference the spike thesis.
Schema validation guarantees shape; this guarantees the plan is actually about
the student's spike.
"""

import re
from collections.abc import Sequence
from typing import Any

from spykt_contracts.models import PlannerResult, PlanTask

SPIKE_ALIGNMENT_THRESHOLD = 0.6


class SpikeAlignmentError(ValueError):
    """Raised when fewer than 60% of plan tasks reference the spike thesis (01-TECH_SPEC §5 A3)."""


def _term_pattern(term: str) -> re.Pattern[str]:
    # Word-boundary match so 'art' does not count inside 'start'.
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def _task_references_spike(task: PlanTask, patterns: Sequence[re.Pattern[str]]) -> bool:
    haystack = "\n".join((task.title, task.spike_alignment, task.rationale))
    return any(pattern.search(haystack) for pattern in patterns)


def validate_planner_result(
    plan: PlannerResult | dict[str, Any],
    spike_thesis_terms: Sequence[str],
) -> float:
    """Enforce the ≥60%-of-tasks-reference-the-spike rule; returns the alignment ratio on success.

    `plan` may be a PlannerResult or a raw wire dict; a dict is first parsed
    through PlannerResult.model_validate (the pydantic mirror of the a3_planner
    schema), so malformed dicts fail structurally before any semantic check.
    A task references the spike when any thesis term appears (word-boundary,
    case-insensitive) in its title, spike_alignment, or rationale. Raises
    SpikeAlignmentError below threshold, ValueError if no usable thesis terms
    are supplied.
    """
    result = plan if isinstance(plan, PlannerResult) else PlannerResult.model_validate(plan)
    terms = [term.strip() for term in spike_thesis_terms if term and term.strip()]
    if not terms:
        raise ValueError("spike_thesis_terms is empty; cannot check spike alignment (01-TECH_SPEC §5 A3)")

    patterns = [_term_pattern(term) for term in terms]
    tasks = result.week_plan.tasks
    if not tasks:
        # Zero-task plans are contract-valid (protected weeks, 01-TECH_SPEC §6):
        # with no tasks, the ≥60% rule is vacuously satisfied.
        return 1.0
    aligned = sum(1 for task in tasks if _task_references_spike(task, patterns))
    ratio = aligned / len(tasks)
    if ratio < SPIKE_ALIGNMENT_THRESHOLD:
        raise SpikeAlignmentError(
            f"Only {aligned}/{len(tasks)} tasks ({ratio:.0%}) reference the spike thesis; "
            f"≥{SPIKE_ALIGNMENT_THRESHOLD:.0%} required (01-TECH_SPEC §5 A3)."
        )
    return ratio
