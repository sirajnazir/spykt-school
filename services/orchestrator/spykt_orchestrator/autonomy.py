"""Server-side autonomy enforcement (PRD §6.1; 01-TECH_SPEC §6; CLAUDE.md Phase 2 gate).

The Orchestrator is the single enforcement point: action class → required level →
blocked until the consent artifact (approval rows) exists. **Enforcement is
server-side; UI is advisory.**

Deliberate absences (they are the security property, do not add them back):
- no bypass/force/dry_run parameter on `authorize`,
- no environment-variable override,
- unknown action types FAIL CLOSED (blocked + class-4 escalation hint, never raised),
- the only path to `allowed=True` is L0 or qualifying approval rows in the store.

Every decision — allow or block — writes an append-only audit row
(action='autonomy_allow' | 'autonomy_block'), so each enforcement is traceable
(PRD §7.5 / §6.4 "show me why").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psycopg
import yaml

AUDIT_AGENT = "orchestrator"
ACTION_ALLOW = "autonomy_allow"
ACTION_BLOCK = "autonomy_block"

# action_type → approvals.subject_type (0002_safety_spine.sql check constraint).
# Identity for every action except weekly_plan_commit: the consent artifact
# attaches to the plan itself ('weekly_plan').
_SUBJECT_TYPE_OVERRIDES = {"weekly_plan_commit": "weekly_plan"}

# Required approver roles per level (PRD §6.1 consent paths). L3 is dual sign-off:
# BOTH roles must have an approved row — one alone never suffices.
_REQUIRED_ROLES: dict[str, frozenset[str]] = {
    "L1": frozenset({"student"}),
    "L2": frozenset({"coach"}),
    "L3": frozenset({"parent", "coach"}),
}


def find_autonomy_yaml(start: Path | None = None) -> Path:
    """Walk up from `start` (or this file) to the repo-root autonomy.yaml."""
    here = start or Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "autonomy.yaml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("autonomy.yaml not found walking up from " + str(here))


@dataclass(frozen=True)
class AutonomyConfig:
    """Action-class → required-consent-level map (autonomy.yaml — config, not code)."""

    levels: dict[str, str]
    actions: dict[str, str]

    def level_for(self, action_type: str) -> str | None:
        """Required level for an action, or None if the action is unknown (fail closed)."""
        return self.actions.get(action_type)


def load_autonomy_config(path: Path | None = None) -> AutonomyConfig:
    raw = yaml.safe_load((path or find_autonomy_yaml()).read_text())
    # Strict keys: an autonomy.yaml missing either section must fail HERE, loudly,
    # rather than silently mapping every action to "unknown" (spykt_anthropic_client
    # config idiom).
    return AutonomyConfig(levels=raw["levels"], actions=raw["actions"])


@dataclass(frozen=True)
class ApprovalRow:
    """One consent artifact (approvals table, 0002_safety_spine.sql)."""

    approver_role: str  # 'student' | 'coach' | 'parent' — matched exactly, case-sensitive
    approver_clerk_id: str
    decision: str  # 'approved' | 'rejected' — only exactly 'approved' counts
    level: str  # 'L1' | 'L2' | 'L3' — must equal the required level to count


class ConsentStore(Protocol):
    """Read side of the approvals table; the store scopes rows to one consent subject."""

    def approvals_for(self, student_id: str, subject_type: str, subject_id: str) -> list[ApprovalRow]: ...


class InMemoryConsentStore:
    """Test double keyed exactly like the approvals table (student, subject_type, subject_id)."""

    def __init__(self) -> None:
        self._rows: list[tuple[str, str, str, ApprovalRow]] = []

    def add(
        self,
        *,
        student_id: str,
        subject_type: str,
        subject_id: str,
        approver_role: str,
        approver_clerk_id: str,
        decision: str,
        level: str,
    ) -> None:
        row = ApprovalRow(
            approver_role=approver_role,
            approver_clerk_id=approver_clerk_id,
            decision=decision,
            level=level,
        )
        self._rows.append((student_id, subject_type, subject_id, row))

    def approvals_for(self, student_id: str, subject_type: str, subject_id: str) -> list[ApprovalRow]:
        return [
            row
            for (sid, stype, sub, row) in self._rows
            if sid == student_id and stype == subject_type and sub == subject_id
        ]


class PostgresConsentStore:
    """SELECT-only reader over the approvals table.

    Accepts either a conninfo string (connection opened and owned by the store) or an
    existing psycopg connection (caller keeps ownership, e.g. one under
    `set role service_role`) — same convention as spykt_audit.PostgresAuditWriter.
    """

    _SELECT_SQL = (
        "SELECT approver_role, approver_clerk_id, decision, level FROM approvals"
        " WHERE student_id = %s AND subject_type = %s AND subject_id = %s"
    )

    def __init__(self, conn: str | psycopg.Connection) -> None:
        if isinstance(conn, str):
            self._conn = psycopg.connect(conn)
            self._owns_conn = True
        else:
            self._conn = conn
            self._owns_conn = False

    def approvals_for(self, student_id: str, subject_type: str, subject_id: str) -> list[ApprovalRow]:
        with self._conn.cursor() as cur:
            cur.execute(self._SELECT_SQL, (student_id, subject_type, subject_id))
            rows = cur.fetchall()
        return [
            ApprovalRow(approver_role=role, approver_clerk_id=clerk, decision=decision, level=level)
            for (role, clerk, decision, level) in rows
        ]

    def close(self) -> None:
        """Close the connection if this store opened it (no-op for caller-owned connections)."""
        if self._owns_conn:
            self._conn.close()


@dataclass(frozen=True)
class Decision:
    """Outcome of one enforcement check. `escalate_hint` is set only on fail-closed
    unknown actions so the Orchestrator routes them to a class-4 escalation (PRD §6.2)."""

    allowed: bool
    required_level: str | None
    reason: str
    escalate_hint: str | None = None


class _AuditWriter(Protocol):
    """Duck type of spykt_audit writers (keyword-only `write`)."""

    def write(
        self,
        *,
        agent: str,
        model: str | None = ...,
        prompt_version: str | None = ...,
        action: str,
        autonomy_level: str | None = ...,
        human_approver: str | None = ...,
        student_id: str | None = ...,
    ) -> None: ...


def subject_type_for(action_type: str) -> str:
    """approvals.subject_type an action's consent artifact is filed under."""
    return _SUBJECT_TYPE_OVERRIDES.get(action_type, action_type)


def authorize(
    action_type: str,
    student_id: str,
    subject_id: str,
    store: ConsentStore,
    audit_writer: _AuditWriter,
    config: AutonomyConfig | None = None,
) -> Decision:
    """Server-side autonomy check for one action. Never raises on bad input: every
    failure mode returns a blocked Decision (fail closed) and is audit-logged."""
    cfg = config or load_autonomy_config()
    level = cfg.level_for(action_type)

    if not (student_id and student_id.strip()) or not (subject_id and subject_id.strip()):
        return _decide(
            audit_writer,
            allowed=False,
            level=level,
            student_id=student_id or None,
            reason="invalid_identifiers",
        )

    if level is None:
        # FAIL CLOSED on unknown action classes; the Orchestrator routes the hint
        # to a class-4 (low confidence) escalation instead of this function raising.
        return _decide(
            audit_writer,
            allowed=False,
            level=None,
            student_id=student_id,
            reason="unknown_action",
            escalate_hint="class_4",
        )

    if level == "L0":
        return _decide(audit_writer, allowed=True, level="L0", student_id=student_id, reason="autonomous")

    required_roles = _REQUIRED_ROLES.get(level)
    if required_roles is None:
        # Config declares a level this enforcement code has no consent path for → fail closed.
        return _decide(
            audit_writer, allowed=False, level=level, student_id=student_id, reason="unknown_level"
        )

    rows = store.approvals_for(student_id, subject_type_for(action_type), subject_id)
    qualifying = [
        row
        for row in rows
        if row.decision == "approved"  # rejected rows never count
        and row.level == level  # approval granted at a different level never counts
        and row.approver_role in required_roles  # exact, case-sensitive role match
    ]
    approved_roles = {row.approver_role for row in qualifying}
    if required_roles <= approved_roles and level == "L3":
        # Dual sign-off means two distinct HUMANS (PRD §6.1 "Parent + coach"), not one
        # clerk id holding both roles.
        if len({row.approver_clerk_id for row in qualifying}) < 2:
            return _decide(
                audit_writer,
                allowed=False,
                level=level,
                student_id=student_id,
                reason="dual_signoff_requires_two_distinct_humans",
            )
    if required_roles <= approved_roles:
        approvers = ",".join(sorted({row.approver_clerk_id for row in qualifying}))
        return _decide(
            audit_writer,
            allowed=True,
            level=level,
            student_id=student_id,
            reason="consent_artifacts_present",
            human_approver=approvers,
        )

    missing = ",".join(sorted(required_roles - approved_roles))
    return _decide(
        audit_writer,
        allowed=False,
        level=level,
        student_id=student_id,
        reason=f"missing_approvals:{missing}",
    )


def _decide(
    audit_writer: _AuditWriter,
    *,
    allowed: bool,
    level: str | None,
    student_id: str | None,
    reason: str,
    escalate_hint: str | None = None,
    human_approver: str | None = None,
) -> Decision:
    """Single exit path: EVERY decision (allow or block) writes an audit row."""
    audit_writer.write(
        agent=AUDIT_AGENT,
        action=ACTION_ALLOW if allowed else ACTION_BLOCK,
        autonomy_level=level,
        human_approver=human_approver,
        student_id=student_id,
    )
    return Decision(allowed=allowed, required_level=level, reason=reason, escalate_hint=escalate_hint)
