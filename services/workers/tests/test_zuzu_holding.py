"""Tests for the Zuzu holding pattern (PRD §6.2.1, 02-UIUX §2.3, GAP-08, PRD §3)."""

import pytest

from spykt_workers import holding_copy
from spykt_workers.zuzu_holding import (
    SUPPRESSED_CONTEXT_KEYS,
    HoldingPattern,
    HoldingPatternError,
    build_holding_system_fragment,
    coach_looped_in_message,
    enter_holding_pattern,
    exit_holding_pattern,
    suppress_coaching_context,
)

# Therapy-speak / diagnosis language banned by PRD §3, and coaching-pressure
# language banned from the static copy by PRD §6.2.1.
BANNED_CLINICAL_TERMS = (
    "diagnos",
    "disorder",
    "depression",
    "anxiety",
    "clinical",
    "therapy",
    "therapist",
    "symptom",
    "treatment",
    "mental illness",
)
BANNED_COACHING_TERMS = ("task", "deadline", "streak", "plan", "commitment", "goal")


def make_pattern(student_id: str = "stu-1") -> HoldingPattern:
    return enter_holding_pattern(escalation_id="esc-123", student_id=student_id, fired_alert=True)


# --- Construction: no escalation / no fired alert -> no holding pattern -------


def test_enter_requires_fired_alert():
    with pytest.raises(HoldingPatternError):
        enter_holding_pattern(escalation_id="esc-123", student_id="stu-1", fired_alert=False)


def test_enter_requires_escalation_id():
    with pytest.raises(HoldingPatternError):
        enter_holding_pattern(escalation_id="", student_id="stu-1", fired_alert=True)


def test_enter_rejects_truthy_non_true_fired_alert():
    # "never fake it": only the literal True from a fired Sentinel alert counts.
    with pytest.raises(HoldingPatternError):
        enter_holding_pattern(escalation_id="esc-123", student_id="stu-1", fired_alert=1)  # type: ignore[arg-type]


def test_direct_construction_cannot_dodge_the_gate():
    with pytest.raises(HoldingPatternError):
        HoldingPattern(student_id="stu-1", escalation_id="", fired_alert=True)
    with pytest.raises(HoldingPatternError):
        HoldingPattern(student_id="stu-1", escalation_id="esc-123", fired_alert=False)


def test_valid_entry_is_keyed_by_student_and_active():
    pattern = make_pattern("stu-42")
    assert pattern.student_id == "stu-42"
    assert pattern.escalation_id == "esc-123"
    assert pattern.active is True
    assert pattern.resolved_by_coach_id is None


# --- Coach-looped-in message: impossible without a pattern (= Sentinel fired) --


def test_coach_message_requires_a_holding_pattern():
    with pytest.raises(HoldingPatternError):
        coach_looped_in_message(None, "Maya")  # type: ignore[arg-type]
    with pytest.raises(HoldingPatternError):
        coach_looped_in_message({"student_id": "stu-1"}, "Maya")  # type: ignore[arg-type]


def test_coach_message_requires_coach_name():
    with pytest.raises(HoldingPatternError):
        coach_looped_in_message(make_pattern(), "")


def test_coach_message_refused_after_resolution():
    pattern = make_pattern()
    exit_holding_pattern(pattern, resolved_by_coach_id="coach-9")
    with pytest.raises(HoldingPatternError):
        coach_looped_in_message(pattern, "Maya")


def test_coach_message_matches_uiux_spec():
    msg = coach_looped_in_message(make_pattern(), "Maya")
    assert msg == "I've looped in Coach Maya, she'll reach out today."


# --- System fragment: crisis resources in, coaching pressure out ---------------


def test_fragment_contains_static_crisis_resources():
    fragment = build_holding_system_fragment(make_pattern())
    assert "988" in fragment
    assert "text HOME to 741741" in fragment
    assert holding_copy.CRISIS_RESOURCES in fragment
    assert holding_copy.SUPPORTIVE_HOLDING_COPY in fragment


def test_fragment_instructs_no_coaching_pressure():
    fragment = build_holding_system_fragment(make_pattern())
    assert "NO plan, task, or deadline pressure" in fragment
    assert "NO new commitments" in fragment


def test_fragment_requires_active_pattern():
    with pytest.raises(HoldingPatternError):
        build_holding_system_fragment(None)  # type: ignore[arg-type]
    pattern = make_pattern()
    exit_holding_pattern(pattern, resolved_by_coach_id="coach-9")
    with pytest.raises(HoldingPatternError):
        build_holding_system_fragment(pattern)


def test_static_copy_has_no_task_or_clinical_language():
    copy_text = holding_copy.HOLDING_COPY.lower()
    for term in BANNED_CLINICAL_TERMS + BANNED_COACHING_TERMS:
        assert term not in copy_text, f"banned term {term!r} found in holding copy"


def test_fragment_has_no_clinical_language():
    fragment = build_holding_system_fragment(make_pattern()).lower()
    # The fragment may *name* plans/tasks to forbid them, but never uses
    # therapy-speak or diagnosis language (PRD §3).
    for term in ("disorder", "depression", "anxiety", "clinical", "symptom", "treatment"):
        assert term not in fragment, f"banned clinical term {term!r} found in fragment"


# --- Context suppression --------------------------------------------------------


def test_suppress_removes_coaching_keys_and_preserves_others():
    pinned = {
        "plan": {"week": 12},
        "tasks": ["essay outline"],
        "commitments": ["daily reading"],
        "streak": 9,
        "streaks": {"reading": 9},
        "deadlines": ["2026-07-10"],
        "student_name": "Sam",
        "genome_summary": "curious builder",
    }
    suppressed = suppress_coaching_context(pinned)
    for key in SUPPRESSED_CONTEXT_KEYS:
        assert key not in suppressed
    assert suppressed["student_name"] == "Sam"
    assert suppressed["genome_summary"] == "curious builder"
    assert suppressed["holding_pattern"] is True


def test_suppress_returns_a_copy():
    pinned = {"plan": {"week": 12}, "student_name": "Sam"}
    suppress_coaching_context(pinned)
    assert pinned == {"plan": {"week": 12}, "student_name": "Sam"}


# --- COPY_STATUS: cannot silently ship as approved ------------------------------


def test_copy_status_flag_exists_and_is_pending_until_g2():
    assert hasattr(holding_copy, "COPY_STATUS")
    # Flipping this constant requires a recorded human sign-off at gate G2.
    assert holding_copy.COPY_STATUS == "PENDING_G2_HUMAN_APPROVAL"


# --- Exit: coach resolution only -------------------------------------------------


def test_exit_requires_coach_id():
    with pytest.raises(HoldingPatternError):
        exit_holding_pattern(make_pattern(), resolved_by_coach_id="")
    with pytest.raises(HoldingPatternError):
        exit_holding_pattern(make_pattern(), resolved_by_coach_id=None)  # type: ignore[arg-type]


def test_exit_by_coach_deactivates_and_records_resolver():
    pattern = make_pattern()
    exited = exit_holding_pattern(pattern, resolved_by_coach_id="coach-9")
    assert exited is pattern
    assert pattern.active is False
    assert pattern.resolved_by_coach_id == "coach-9"


def test_exit_twice_is_refused():
    pattern = make_pattern()
    exit_holding_pattern(pattern, resolved_by_coach_id="coach-9")
    with pytest.raises(HoldingPatternError):
        exit_holding_pattern(pattern, resolved_by_coach_id="coach-10")
