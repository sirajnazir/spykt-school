"""Escalation queue + delivery (PRD §6.2, GAP-08/OD-5; 01-TECH_SPEC §3 escalations/oncall).

Class semantics (PRD §6.2, unconditional Sentinel → human):
  1. Wellbeing — immediate coach alert. The row is created AND push+SMS to the assigned
     coach + all active on-call coaches fire SYNCHRONOUSLY inside handle(), before it
     returns — class-1 bypasses queue ordering. SLA: 15 min to acknowledge (GAP-08);
     past-due unacknowledged rows fan out to the admin phone tree via check_overdue().
  2. Family conflict / pressure — coach review within 24h (SLA row only).
  3. Integrity — coach. PRD §6.2 sets NO SLA for class-3; the 48h here is a
     conservative builder default borrowed from the L2 approval SLA (PRD §6.1,
     02-UIUX §4.2), not §6.2 semantics.
  4. Low confidence — coach queue row, no SLA timer.
  5. Model refusal — row + log line (engineering visibility per 01 §4.1.1).

Coach escalation delivery is EXEMPT from notification budgets (02-UIUX §0.3) — this
service sends directly through the injected senders and never consults a budget.
Every action (create / alert send / ack / resolve / phone tree) writes an audit row
(PRD §7.5) through the spykt_audit duck type.

Clock, senders, store, audit writer, and the admin phone-tree hook are all injected;
nothing here reads env at import and tests never touch the network (Recorder fakes
from spykt_notify).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable, Protocol, Sequence
from uuid import uuid4

from spykt_notify import Notification, PushSender, SmsSender

if TYPE_CHECKING:
    # psycopg is only needed by PostgresEscalationStore and is imported lazily there:
    # services/workers does not declare psycopg directly (it resolves transitively via
    # spykt-audit today), so the module must stay importable without it.
    import psycopg

logger = logging.getLogger("spykt.workers.escalation")

# Acknowledgement SLA per class. Classes 1 (15 min, GAP-08) and 2 (24h, PRD §6.2) are
# spec-derived. Class 3 is a BUILDER DEFAULT: PRD §6.2 sets no SLA for integrity — 48h
# is borrowed (conservatively) from the L2 approval SLA (PRD §6.1, 02-UIUX §4.2).
# Class 4 sits in the coach queue with no timer; class 5 likewise (engineering
# visibility, not a paged alert).
SLA_BY_CLASS: dict[int, timedelta | None] = {
    1: timedelta(minutes=15),
    2: timedelta(hours=24),
    3: timedelta(hours=48),
    4: None,
    5: None,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def coach_deep_link(escalation_id: str) -> str:
    """Deep link into the coach console escalation queue (02-UIUX §4.1, §5)."""
    return f"/coach/escalations/{escalation_id}"


@dataclass(frozen=True)
class CoachContact:
    """A reachable coach: push alias is the coach id; SMS needs the phone number.

    `active`/`priority` mirror the oncall table (migration 0002) so rotation rows
    map straight onto handle()'s oncall argument.

    INTEGRATION SEAM (PRD §6.2 / GAP-08 class-1 push+SMS): `phone` is NOT persisted
    anywhere in the schema — the coaches table (migration 0001) and oncall table
    (migration 0002) have no phone column, and list_active_oncall() returns coach
    ids only. Production callers must join coach ids to phone numbers from the
    identity provider (Clerk profile metadata) or deployment config when building
    the CoachContact list for handle().
    """

    coach_id: str
    phone: str
    active: bool = True
    priority: int = 1


@dataclass(frozen=True)
class Escalation:
    """One escalations-table row (01-TECH_SPEC §3 + migration 0002 ack columns)."""

    id: str
    student_id: str
    escalation_class: int
    severity: str
    payload: dict[str, Any]
    assigned_coach: str | None
    sla_due: datetime | None
    created_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    phone_tree_fired_at: datetime | None = None


class EscalationStore(Protocol):
    """Storage contract for the escalations table (create/ack/resolve/list_overdue)."""

    def create(
        self,
        *,
        student_id: str,
        escalation_class: int,
        severity: str,
        payload: dict[str, Any],
        assigned_coach: str | None,
        sla_due: datetime | None,
        created_at: datetime,
    ) -> Escalation: ...

    def ack(self, escalation_id: str, coach_id: str, at: datetime) -> Escalation: ...

    def resolve(self, escalation_id: str, coach_id: str, at: datetime) -> Escalation: ...

    def list_overdue(self, now: datetime) -> list[Escalation]:
        """Class-1 rows unacknowledged and unresolved past sla_due (GAP-08)."""
        ...

    def mark_phone_tree_fired(self, escalation_id: str, at: datetime) -> None:
        """Record the admin phone-tree fan-out so it fires once per escalation."""
        ...


class InMemoryEscalationStore:
    """Dict-backed EscalationStore for tests and local runs."""

    def __init__(self) -> None:
        self.rows: dict[str, Escalation] = {}

    def create(
        self,
        *,
        student_id: str,
        escalation_class: int,
        severity: str,
        payload: dict[str, Any],
        assigned_coach: str | None,
        sla_due: datetime | None,
        created_at: datetime,
    ) -> Escalation:
        row = Escalation(
            id=str(uuid4()),
            student_id=student_id,
            escalation_class=escalation_class,
            severity=severity,
            payload=dict(payload),
            assigned_coach=assigned_coach,
            sla_due=sla_due,
            created_at=created_at,
        )
        self.rows[row.id] = row
        return row

    def _get(self, escalation_id: str) -> Escalation:
        row = self.rows.get(escalation_id)
        if row is None:
            raise KeyError(f"unknown escalation id {escalation_id!r}")
        return row

    def ack(self, escalation_id: str, coach_id: str, at: datetime) -> Escalation:
        row = replace(self._get(escalation_id), acknowledged_at=at, acknowledged_by=coach_id)
        self.rows[escalation_id] = row
        return row

    def resolve(self, escalation_id: str, coach_id: str, at: datetime) -> Escalation:
        row = replace(self._get(escalation_id), resolved_at=at)
        self.rows[escalation_id] = row
        return row

    def list_overdue(self, now: datetime) -> list[Escalation]:
        return [
            row
            for row in self.rows.values()
            if row.escalation_class == 1
            and row.acknowledged_at is None
            and row.resolved_at is None
            and row.sla_due is not None
            and row.sla_due <= now
        ]

    def mark_phone_tree_fired(self, escalation_id: str, at: datetime) -> None:
        row = self._get(escalation_id)
        if row.phone_tree_fired_at is not None:
            return  # first mark wins — the fired timestamp is never overwritten
        self.rows[escalation_id] = replace(row, phone_tree_fired_at=at)


# The escalations table has no dedicated phone-tree column (migration 0002); the fired
# marker rides in the payload jsonb so idempotency survives worker restarts.
_PHONE_TREE_KEY = "phone_tree_fired_at"

_SELECT_COLUMNS = (
    "id, student_id, class, severity, payload, assigned_coach, sla_due,"
    " created_at, acknowledged_at, acknowledged_by, resolved_at"
)


class PostgresEscalationStore:
    """EscalationStore against the escalations table (01-TECH_SPEC §3, migration 0002).

    Accepts a conninfo string (connection owned by the store) or an existing psycopg
    connection (caller-owned, e.g. one already under `set role service_role`) —
    same convention as spykt_audit.PostgresAuditWriter.
    """

    def __init__(self, conn: str | psycopg.Connection) -> None:
        if isinstance(conn, str):
            import psycopg  # lazy: keep spykt_workers.escalation importable without psycopg

            self._conn = psycopg.connect(conn)
            self._owns_conn = True
        else:
            self._conn = conn
            self._owns_conn = False

    def close(self) -> None:
        if self._owns_conn:
            self._conn.close()

    @staticmethod
    def _row_to_escalation(row: tuple) -> Escalation:
        payload = row[4] or {}
        fired_raw = payload.get(_PHONE_TREE_KEY)
        return Escalation(
            id=str(row[0]),
            student_id=str(row[1]),
            escalation_class=row[2],
            severity=row[3],
            payload=payload,
            assigned_coach=str(row[5]) if row[5] is not None else None,
            sla_due=row[6],
            created_at=row[7],
            acknowledged_at=row[8],
            acknowledged_by=str(row[9]) if row[9] is not None else None,
            resolved_at=row[10],
            phone_tree_fired_at=datetime.fromisoformat(fired_raw) if fired_raw else None,
        )

    def _fetch(self, escalation_id: str) -> Escalation:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM escalations WHERE id = %s", (escalation_id,)
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError(f"unknown escalation id {escalation_id!r}")
        return self._row_to_escalation(row)

    def create(
        self,
        *,
        student_id: str,
        escalation_class: int,
        severity: str,
        payload: dict[str, Any],
        assigned_coach: str | None,
        sla_due: datetime | None,
        created_at: datetime,
    ) -> Escalation:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO escalations"
                " (student_id, class, severity, payload, assigned_coach, sla_due, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    student_id,
                    escalation_class,
                    severity,
                    json.dumps(payload),
                    assigned_coach,
                    sla_due,
                    created_at,
                ),
            )
            escalation_id = str(cur.fetchone()[0])
        self._conn.commit()
        return self._fetch(escalation_id)

    def ack(self, escalation_id: str, coach_id: str, at: datetime) -> Escalation:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE escalations SET acknowledged_at = %s, acknowledged_by = %s WHERE id = %s",
                (at, coach_id, escalation_id),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                raise KeyError(f"unknown escalation id {escalation_id!r}")
        self._conn.commit()
        return self._fetch(escalation_id)

    def resolve(self, escalation_id: str, coach_id: str, at: datetime) -> Escalation:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE escalations SET resolved_at = %s WHERE id = %s", (at, escalation_id)
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                raise KeyError(f"unknown escalation id {escalation_id!r}")
        self._conn.commit()
        return self._fetch(escalation_id)

    def list_overdue(self, now: datetime) -> list[Escalation]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM escalations"
                " WHERE class = 1 AND acknowledged_at IS NULL AND resolved_at IS NULL"
                " AND sla_due IS NOT NULL AND sla_due <= %s"
                " ORDER BY sla_due",
                (now,),
            )
            rows = cur.fetchall()
        return [self._row_to_escalation(row) for row in rows]

    def mark_phone_tree_fired(self, escalation_id: str, at: datetime) -> None:
        # Compare-and-set: only the first mark lands (the fired timestamp is never
        # overwritten by a racing sweep). NOTE this narrows but does not eliminate
        # the concurrent double-fire window — see check_overdue's docstring.
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE escalations SET payload = payload || %s::jsonb"
                " WHERE id = %s AND NOT (payload ? %s)",
                (json.dumps({_PHONE_TREE_KEY: at.isoformat()}), escalation_id, _PHONE_TREE_KEY),
            )
            if cur.rowcount == 0:
                # Distinguish "already marked" (benign no-op) from an unknown id.
                cur.execute("SELECT 1 FROM escalations WHERE id = %s", (escalation_id,))
                if cur.fetchone() is None:
                    self._conn.rollback()
                    raise KeyError(f"unknown escalation id {escalation_id!r}")
        self._conn.commit()

    def list_active_oncall(self) -> list[str]:
        """Active on-call coach ids in rotation priority order (oncall table, GAP-08)."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT coach_id FROM oncall WHERE active ORDER BY priority, created_at")
            return [str(row[0]) for row in cur.fetchall()]


class AuditWriter(Protocol):
    """Duck type of spykt_audit writers (depend on the Protocol, not the package)."""

    def write(
        self,
        *,
        agent: str,
        model: str | None = None,
        prompt_version: str | None = None,
        action: str,
        autonomy_level: str | None = None,
        human_approver: str | None = None,
        student_id: str | None = None,
    ) -> None: ...


class EscalationService:
    """Sentinel output → escalation rows + coach delivery + SLA enforcement (PRD §6.2)."""

    AGENT = "escalation_service"

    def __init__(
        self,
        store: EscalationStore,
        push: PushSender,
        sms: SmsSender,
        audit: AuditWriter,
        *,
        phone_tree: Callable[[Escalation], None] | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.store = store
        self.push = push
        self.sms = sms
        self.audit = audit
        self.phone_tree = phone_tree
        self.clock = clock

    def handle(
        self,
        sentinel_output: Any,
        *,
        student_id: str,
        assigned_coach: CoachContact,
        oncall: Sequence[CoachContact],
    ) -> Escalation:
        """Create the escalation row; class-1 also alerts coaches before returning.

        `sentinel_output` is a SentinelResult (spykt_contracts) or any object with
        `escalation_class` / `severity` / `evidence_ref` / `recommended_action`.

        Class-1 failure semantics (PRD §6.2: "immediate coach alert (push + SMS)"):
        every channel to every recipient is ATTEMPTED regardless of individual
        failures — a dead push provider must not stop the redundant SMS, and one
        unreachable coach must not stop the rest of the fan-out. Even an audit-writer
        outage cannot suppress the alerts (the row + 15-min phone-tree backstop are
        already armed). If anything failed, an ExceptionGroup is raised AFTER all
        attempts so callers/operators see it; nothing is silently swallowed.
        """
        escalation_class = int(sentinel_output.escalation_class)
        if escalation_class not in SLA_BY_CLASS:
            raise ValueError(f"escalation class must be 1-5 (PRD §6.2), got {escalation_class}")
        now = self.clock()
        sla = SLA_BY_CLASS[escalation_class]
        row = self.store.create(
            student_id=student_id,
            escalation_class=escalation_class,
            severity=str(sentinel_output.severity),
            payload={
                "evidence_ref": str(sentinel_output.evidence_ref),
                "recommended_action": str(sentinel_output.recommended_action),
            },
            assigned_coach=assigned_coach.coach_id,
            sla_due=(now + sla) if sla is not None else None,
            created_at=now,
        )
        created_action = f"escalation_created:class-{escalation_class}"

        if escalation_class == 1:
            # PRD §6.2.1 semantics: immediate coach alert, push + SMS, synchronously —
            # class-1 bypasses queue ordering; nothing may defer this fan-out. The
            # created-audit write is attempted first but must never block the alerts.
            failures: list[Exception] = []
            try:
                self.audit.write(agent=self.AGENT, action=created_action, student_id=student_id)
            except Exception as exc:
                logger.exception(
                    "audit write %r failed for class-1 escalation %s; alert fan-out proceeds",
                    created_action,
                    row.id,
                )
                failures.append(exc)
            failures.extend(self._alert_coaches(row, assigned_coach, oncall))
            if failures:
                raise ExceptionGroup(
                    f"class-1 escalation {row.id} delivery partially failed"
                    " (row created; all channels/recipients were attempted;"
                    " 15-min phone-tree backstop remains armed)",
                    failures,
                )
            return row

        self.audit.write(agent=self.AGENT, action=created_action, student_id=student_id)
        if escalation_class == 5:
            # Engineering visibility (01 §4.1.1): the row lands in the coach queue and
            # the log line is the engineering signal.
            logger.warning(
                "class-5 model-refusal escalation %s for student %s: %s",
                row.id,
                student_id,
                row.payload.get("evidence_ref"),
            )
        return row

    def _alert_coaches(
        self, row: Escalation, assigned_coach: CoachContact, oncall: Sequence[CoachContact]
    ) -> list[Exception]:
        """Push + SMS to every recipient with per-send failure isolation.

        This is the class-1 safety path: SMS is exactly the redundant channel for
        when push is down, so no single sender/recipient failure may abort the rest.
        All failures are logged and returned for handle() to raise as a group.
        """
        recipients: list[CoachContact] = [assigned_coach]
        seen = {assigned_coach.coach_id}
        for coach in oncall:
            if coach.active and coach.coach_id not in seen:
                recipients.append(coach)
                seen.add(coach.coach_id)
        notification = Notification(
            title="Class-1 wellbeing escalation",
            body=(
                f"Severity {row.severity}. Acknowledge within 15 minutes "
                f"(student {row.student_id})."
            ),
            deep_link=coach_deep_link(row.id),
            data={"escalation_id": row.id, "class": 1},
        )
        failures: list[Exception] = []
        for coach in recipients:
            delivered = 0
            channels = (
                ("push", self.push.send, coach.coach_id),
                ("sms", self.sms.send, coach.phone),
            )
            for channel, send, to in channels:
                try:
                    send(to=to, notification=notification)
                    delivered += 1
                except Exception as exc:
                    logger.exception(
                        "class-1 %s alert to coach %s failed (escalation %s, student %s)",
                        channel,
                        coach.coach_id,
                        row.id,
                        row.student_id,
                    )
                    failures.append(exc)
            outcome = "sent" if delivered else "failed"
            try:
                self.audit.write(
                    agent=self.AGENT,
                    action=f"class1_alert_{outcome}:{coach.coach_id}",
                    student_id=row.student_id,
                )
            except Exception as exc:
                logger.exception(
                    "audit write for class-1 alert to coach %s failed (escalation %s)",
                    coach.coach_id,
                    row.id,
                )
                failures.append(exc)
        return failures

    def acknowledge(self, escalation_id: str, coach_id: str) -> Escalation:
        row = self.store.ack(escalation_id, coach_id, self.clock())
        self.audit.write(
            agent=self.AGENT,
            action="escalation_acknowledged",
            human_approver=coach_id,
            student_id=row.student_id,
        )
        return row

    def resolve(self, escalation_id: str, coach_id: str) -> Escalation:
        row = self.store.resolve(escalation_id, coach_id, self.clock())
        self.audit.write(
            agent=self.AGENT,
            action="escalation_resolved",
            human_approver=coach_id,
            student_id=row.student_id,
        )
        return row

    def check_overdue(self, now: datetime | None = None) -> list[Escalation]:
        """GAP-08: class-1 unacknowledged past sla_due → admin phone tree, once per row.

        Idempotent across sequential sweeps (and worker restarts, via the store's
        fired marker): a SINGLE sweeper fires each escalation exactly once. The
        fire-then-mark sequence is deliberately NOT atomic — two concurrent sweeper
        replicas, or a crash between phone_tree(row) and mark, can re-fire. That
        failure direction is chosen on purpose: a class-1 wellbeing page may
        over-alert but must never be silently dropped (marking before firing would
        risk exactly that on a crash). Deploy one sweeper replica, or accept the
        occasional duplicate page.
        """
        at = now if now is not None else self.clock()
        fired: list[Escalation] = []
        for row in self.store.list_overdue(at):
            if row.phone_tree_fired_at is not None:
                continue
            if self.phone_tree is None:
                # GAP-08 makes the phone tree the safety backstop; a missing hook must
                # fail loudly, and the row stays eligible so it fires once wired.
                logger.error(
                    "overdue class-1 escalation %s has NO phone-tree hook wired "
                    "(GAP-08 requires admin phone tree after 15 min unacknowledged)",
                    row.id,
                )
                continue
            self.phone_tree(row)
            self.store.mark_phone_tree_fired(row.id, at)
            self.audit.write(
                agent=self.AGENT,
                action="phone_tree_fired",
                student_id=row.student_id,
            )
            fired.append(row)
        return fired
