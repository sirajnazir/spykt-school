"""InMemoryAuditWriter unit tests (01-TECH_SPEC §3 audit_log; PRD §7.5)."""

import pytest

from spykt_audit import InMemoryAuditWriter, PostgresAuditWriter

EXPECTED_COLUMNS = {
    "agent",
    "model",
    "prompt_version",
    "action",
    "autonomy_level",
    "human_approver",
    "student_id",
}


def test_write_records_full_row():
    w = InMemoryAuditWriter()
    w.write(
        agent="pathway_planner",
        model="claude-fable-5",
        prompt_version="pp-v3",
        action="plan_proposed",
        autonomy_level="L2",
        human_approver="coach_7",
        student_id="11111111-1111-1111-1111-111111111111",
    )
    assert w.rows == [
        {
            "agent": "pathway_planner",
            "model": "claude-fable-5",
            "prompt_version": "pp-v3",
            "action": "plan_proposed",
            "autonomy_level": "L2",
            "human_approver": "coach_7",
            "student_id": "11111111-1111-1111-1111-111111111111",
        }
    ]


def test_optional_fields_default_to_none():
    w = InMemoryAuditWriter()
    w.write(agent="sentinel", action="escalation_raised")
    (row,) = w.rows
    assert set(row) == EXPECTED_COLUMNS
    assert row["agent"] == "sentinel"
    assert row["action"] == "escalation_raised"
    for col in EXPECTED_COLUMNS - {"agent", "action"}:
        assert row[col] is None


def test_writes_append_in_order():
    w = InMemoryAuditWriter()
    w.write(agent="zuzu", action="turn_1")
    w.write(agent="zuzu", action="turn_2")
    assert [r["action"] for r in w.rows] == ["turn_1", "turn_2"]


@pytest.mark.parametrize("agent", ["", None])
def test_empty_agent_rejected(agent):
    w = InMemoryAuditWriter()
    with pytest.raises(ValueError):
        w.write(agent=agent, action="something")
    assert w.rows == []


@pytest.mark.parametrize("action", ["", None])
def test_empty_action_rejected(action):
    w = InMemoryAuditWriter()
    with pytest.raises(ValueError):
        w.write(agent="zuzu", action=action)
    assert w.rows == []


def test_write_is_keyword_only():
    """The cross-package duck type takes keyword-only arguments."""
    w = InMemoryAuditWriter()
    with pytest.raises(TypeError):
        w.write("zuzu", None, None, "action")  # noqa: PLE1120 — deliberate misuse


@pytest.mark.parametrize("cls", [InMemoryAuditWriter, PostgresAuditWriter])
def test_append_only_by_construction(cls):
    """No UPDATE/DELETE surface exists on either writer (01-TECH_SPEC §10)."""
    public = {name for name in dir(cls) if not name.startswith("_")}
    forbidden = {n for n in public if "update" in n.lower() or "delete" in n.lower()}
    assert not forbidden


def test_writers_share_the_write_signature():
    import inspect

    mem = inspect.signature(InMemoryAuditWriter.write)
    pg = inspect.signature(PostgresAuditWriter.write)
    assert mem == pg
