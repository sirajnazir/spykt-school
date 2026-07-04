"""Attempt-to-bypass tests for server-side autonomy enforcement.

CLAUDE.md Phase 2 gate: "L2/L3 actions blocked without approval rows (attempt-to-bypass
tests)". PRD §6.1 ladder + consent paths; 01-TECH_SPEC §6 "Enforcement is server-side;
UI is advisory". Every path here uses the real repo autonomy.yaml (config, not code).
"""

import inspect

import pytest
from spykt_audit import InMemoryAuditWriter

from spykt_orchestrator.autonomy import (
    ApprovalRow,
    AutonomyConfig,
    Decision,
    InMemoryConsentStore,
    authorize,
    find_autonomy_yaml,
    load_autonomy_config,
    subject_type_for,
)

STUDENT = "student-uuid-1"
OTHER_STUDENT = "student-uuid-2"
SUBJECT = "subject-uuid-1"
OTHER_SUBJECT = "subject-uuid-2"

L2_ACTION = "quarter_roadmap_change"
L3_ACTION = "fee_bearing_application"


@pytest.fixture()
def audit() -> InMemoryAuditWriter:
    return InMemoryAuditWriter()


@pytest.fixture()
def store() -> InMemoryConsentStore:
    return InMemoryConsentStore()


def approve(store, *, role, action=L2_ACTION, student=STUDENT, subject=SUBJECT, decision="approved",
            level=None, clerk="clerk_x"):
    cfg = load_autonomy_config()
    store.add(
        student_id=student,
        subject_type=subject_type_for(action),
        subject_id=subject,
        approver_role=role,
        approver_clerk_id=clerk,
        decision=decision,
        level=level or cfg.level_for(action),
    )


# ---------------------------------------------------------------- config loading


def test_load_autonomy_config_walks_up_to_repo_root():
    path = find_autonomy_yaml()
    assert path.name == "autonomy.yaml"
    cfg = load_autonomy_config(path)
    assert cfg.levels["L3"] == "parent_plus_coach"
    assert cfg.level_for("nudge") == "L0"
    assert cfg.level_for("weekly_plan_commit") == "L1"
    assert cfg.level_for(L2_ACTION) == "L2"
    assert cfg.level_for(L3_ACTION) == "L3"
    assert cfg.level_for("exfiltrate_data") is None


def test_weekly_plan_commit_files_under_weekly_plan_subject_type():
    # approvals.subject_type check constraint has 'weekly_plan', not 'weekly_plan_commit'
    assert subject_type_for("weekly_plan_commit") == "weekly_plan"
    assert subject_type_for(L2_ACTION) == L2_ACTION


# ---------------------------------------------------------------- L0


def test_l0_allowed_and_audited(store, audit):
    d = authorize("nudge", STUDENT, SUBJECT, store, audit)
    assert d.allowed is True
    assert d.required_level == "L0"
    assert audit.rows == [
        {
            "agent": "orchestrator",
            "model": None,
            "prompt_version": None,
            "action": "autonomy_allow",
            "autonomy_level": "L0",
            "human_approver": None,
            "student_id": STUDENT,
        }
    ]


# ---------------------------------------------------------------- L1


def test_l1_blocked_without_student_consent(store, audit):
    d = authorize("weekly_plan_commit", STUDENT, SUBJECT, store, audit)
    assert d.allowed is False
    assert d.required_level == "L1"
    assert d.reason == "missing_approvals:student"


def test_l1_allowed_with_student_approved_row(store, audit):
    approve(store, role="student", action="weekly_plan_commit", clerk="clerk_stu")
    d = authorize("weekly_plan_commit", STUDENT, SUBJECT, store, audit)
    assert d.allowed is True
    assert audit.rows[-1]["action"] == "autonomy_allow"
    assert audit.rows[-1]["human_approver"] == "clerk_stu"


def test_l1_coach_row_does_not_substitute_for_student_consent(store, audit):
    approve(store, role="coach", action="weekly_plan_commit")
    assert authorize("weekly_plan_commit", STUDENT, SUBJECT, store, audit).allowed is False


# ---------------------------------------------------------------- L2 bypass attempts


def test_l2_blocked_with_zero_rows(store, audit):
    d = authorize(L2_ACTION, STUDENT, SUBJECT, store, audit)
    assert d.allowed is False
    assert d.required_level == "L2"
    assert audit.rows[-1]["action"] == "autonomy_block"
    assert audit.rows[-1]["autonomy_level"] == "L2"


def test_l2_blocked_with_student_role_row_only(store, audit):
    approve(store, role="student")
    assert authorize(L2_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l2_blocked_with_rejected_coach_row(store, audit):
    approve(store, role="coach", decision="rejected")
    assert authorize(L2_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l2_blocked_with_coach_row_for_different_subject(store, audit):
    approve(store, role="coach", subject=OTHER_SUBJECT)
    assert authorize(L2_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l2_blocked_with_coach_row_for_different_student(store, audit):
    approve(store, role="coach", student=OTHER_STUDENT)
    assert authorize(L2_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l2_blocked_with_coach_row_for_different_action_subject_type(store, audit):
    approve(store, role="coach", action="spike_thesis_pivot", level="L2")
    assert authorize(L2_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l2_blocked_when_coach_row_recorded_at_wrong_level(store, audit):
    # An approval granted at L1 must never satisfy an L2 requirement.
    approve(store, role="coach", level="L1")
    assert authorize(L2_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l2_allowed_with_qualifying_coach_row(store, audit):
    approve(store, role="coach", clerk="clerk_coach")
    d = authorize(L2_ACTION, STUDENT, SUBJECT, store, audit)
    assert d.allowed is True
    assert d.reason == "consent_artifacts_present"
    assert audit.rows[-1]["human_approver"] == "clerk_coach"


@pytest.mark.parametrize("forged_role", ["Coach", "COACH", "coach ", " coach", "admin", "superuser"])
def test_l2_forged_role_strings_blocked(store, audit, forged_role):
    approve(store, role=forged_role)
    assert authorize(L2_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


# ---------------------------------------------------------------- L3 dual sign-off


def test_l3_blocked_with_only_coach(store, audit):
    approve(store, role="coach", action=L3_ACTION)
    d = authorize(L3_ACTION, STUDENT, SUBJECT, store, audit)
    assert d.allowed is False
    assert d.reason == "missing_approvals:parent"


def test_l3_blocked_with_only_parent(store, audit):
    approve(store, role="parent", action=L3_ACTION)
    d = authorize(L3_ACTION, STUDENT, SUBJECT, store, audit)
    assert d.allowed is False
    assert d.reason == "missing_approvals:coach"


def test_l3_blocked_with_parent_plus_student(store, audit):
    approve(store, role="parent", action=L3_ACTION)
    approve(store, role="student", action=L3_ACTION)
    assert authorize(L3_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l3_blocked_with_same_role_approving_twice(store, audit):
    approve(store, role="coach", action=L3_ACTION, clerk="clerk_a")
    approve(store, role="coach", action=L3_ACTION, clerk="clerk_b")
    assert authorize(L3_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l3_blocked_when_one_of_the_pair_is_rejected(store, audit):
    approve(store, role="coach", action=L3_ACTION)
    approve(store, role="parent", action=L3_ACTION, decision="rejected")
    assert authorize(L3_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


def test_l3_allowed_with_parent_and_coach(store, audit):
    approve(store, role="parent", action=L3_ACTION, clerk="clerk_parent")
    approve(store, role="coach", action=L3_ACTION, clerk="clerk_coach")
    d = authorize(L3_ACTION, STUDENT, SUBJECT, store, audit)
    assert d.allowed is True
    assert d.required_level == "L3"
    assert audit.rows[-1]["human_approver"] == "clerk_coach,clerk_parent"


# ---------------------------------------------------------------- fail-closed paths


def test_unknown_action_fails_closed_without_raising(store, audit):
    d = authorize("exfiltrate_data", STUDENT, SUBJECT, store, audit)
    assert isinstance(d, Decision)
    assert d.allowed is False
    assert d.required_level is None
    assert d.reason == "unknown_action"
    assert d.escalate_hint == "class_4"
    assert audit.rows[-1]["action"] == "autonomy_block"


@pytest.mark.parametrize("student_id,subject_id", [("", SUBJECT), (STUDENT, ""), ("", "")])
def test_empty_identifiers_blocked_even_for_l0(store, audit, student_id, subject_id):
    d = authorize("nudge", student_id, subject_id, store, audit)
    assert d.allowed is False
    assert d.reason == "invalid_identifiers"
    assert audit.rows[-1]["action"] == "autonomy_block"


def test_config_level_without_consent_path_fails_closed(store, audit):
    # A future autonomy.yaml edit adding an unmapped level must block, not allow.
    cfg = AutonomyConfig(levels={"L4": "board_approval"}, actions={"merge_company": "L4"})
    d = authorize("merge_company", STUDENT, SUBJECT, store, audit, config=cfg)
    assert d.allowed is False
    assert d.reason == "unknown_level"


# ---------------------------------------------------------------- no bypass surface


def test_authorize_exposes_no_bypass_parameters():
    params = set(inspect.signature(authorize).parameters)
    assert params == {"action_type", "student_id", "subject_id", "store", "audit_writer", "config"}
    forbidden = {"bypass", "dry_run", "force", "override", "skip_enforcement", "allow"}
    assert params.isdisjoint(forbidden)


def test_env_vars_cannot_flip_a_block_to_allow(store, audit, monkeypatch):
    for var in ("SPYKT_AUTONOMY_BYPASS", "AUTONOMY_BYPASS", "SPYKT_DRY_RUN", "AUTONOMY_OVERRIDE"):
        monkeypatch.setenv(var, "1")
    assert authorize(L2_ACTION, STUDENT, SUBJECT, store, audit).allowed is False
    assert authorize(L3_ACTION, STUDENT, SUBJECT, store, audit).allowed is False


# ---------------------------------------------------------------- audit on every path


def test_every_decision_writes_exactly_one_audit_row(store, audit):
    approve(store, role="coach")
    authorize("nudge", STUDENT, SUBJECT, store, audit)  # allow (L0)
    authorize(L2_ACTION, STUDENT, SUBJECT, store, audit)  # allow (row present)
    authorize(L3_ACTION, STUDENT, SUBJECT, store, audit)  # block
    authorize("exfiltrate_data", STUDENT, SUBJECT, store, audit)  # block (unknown)
    authorize("nudge", "", "", store, audit)  # block (invalid ids)
    assert [r["action"] for r in audit.rows] == [
        "autonomy_allow",
        "autonomy_allow",
        "autonomy_block",
        "autonomy_block",
        "autonomy_block",
    ]
    assert [r["autonomy_level"] for r in audit.rows] == ["L0", "L2", "L3", None, "L0"]
    assert all(r["agent"] == "orchestrator" for r in audit.rows)


def test_approval_row_is_immutable():
    row = ApprovalRow(approver_role="coach", approver_clerk_id="c", decision="rejected", level="L2")
    with pytest.raises(AttributeError):
        row.decision = "approved"  # type: ignore[misc]


def test_l3_blocked_when_one_human_holds_both_roles(store, audit):
    """Dual sign-off = two distinct humans, not one clerk id with two role rows."""
    approve(store, role="parent", action=L3_ACTION, clerk="clerk_same")
    approve(store, role="coach", action=L3_ACTION, clerk="clerk_same")
    d = authorize(L3_ACTION, STUDENT, SUBJECT, store, audit)
    assert d.allowed is False
    assert d.reason == "dual_signoff_requires_two_distinct_humans"


def test_whitespace_only_identifiers_fail_closed(store, audit):
    for student, subject in ((" ", SUBJECT), (STUDENT, "  "), ("\t", "\n")):
        d = authorize("nudge", student, subject, store, audit)
        assert d.allowed is False
        assert d.reason == "invalid_identifiers"
