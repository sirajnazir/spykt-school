"""EscalationService tests (PRD §6.2 classes; GAP-08 on-call + phone tree;
01-TECH_SPEC §3 escalations/oncall; 02-UIUX §0.3 budget exemption, §5 deep links).

Unit tests run against InMemoryEscalationStore + spykt_notify Recorder fakes (no
network, no database). The PostgresEscalationStore integration tests are gated on
DATABASE_URL, mirroring packages/audit/tests/test_postgres_writer.py.
"""

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from spykt_audit import InMemoryAuditWriter
from spykt_contracts import SentinelResult
from spykt_notify import (
    COACH_ESCALATION_CHANNEL,
    BudgetRule,
    NotificationBudget,
    RecorderPush,
    RecorderSms,
)
from spykt_workers.escalation import (
    CoachContact,
    EscalationService,
    InMemoryEscalationStore,
    PostgresEscalationStore,
)

T0 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)

ASSIGNED = CoachContact(coach_id="coach-assigned", phone="+15550000001")
ONCALL_A = CoachContact(coach_id="coach-oncall-a", phone="+15550000002", priority=1)
ONCALL_B = CoachContact(coach_id="coach-oncall-b", phone="+15550000003", priority=2)
ONCALL_INACTIVE = CoachContact(coach_id="coach-off", phone="+15550000004", active=False)


class FixedClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def sentinel(cls: int, severity: str = "high") -> SentinelResult:
    return SentinelResult.model_validate(
        {
            "class": cls,
            "severity": severity,
            "evidence_ref": "transcript:abc#42",
            "recommended_action": "coach outreach today",
        }
    )


class RecordingPhoneTree:
    def __init__(self) -> None:
        self.fired: list[str] = []

    def __call__(self, escalation) -> None:
        self.fired.append(escalation.id)


@pytest.fixture()
def clock():
    return FixedClock()


@pytest.fixture()
def deps(clock):
    return {
        "store": InMemoryEscalationStore(),
        "push": RecorderPush(),
        "sms": RecorderSms(),
        "audit": InMemoryAuditWriter(),
        "phone_tree": RecordingPhoneTree(),
        "clock": clock,
    }


@pytest.fixture()
def service(deps):
    return EscalationService(
        deps["store"],
        deps["push"],
        deps["sms"],
        deps["audit"],
        phone_tree=deps["phone_tree"],
        clock=deps["clock"],
    )


class TestClass1:
    def test_row_created_with_15_minute_sla(self, service, deps):
        row = service.handle(
            sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[ONCALL_A]
        )
        assert row.escalation_class == 1
        assert row.assigned_coach == "coach-assigned"
        assert row.sla_due == T0 + timedelta(minutes=15)
        assert deps["store"].rows[row.id] == row

    def test_push_and_sms_fire_synchronously_to_assigned_plus_active_oncall(self, service, deps):
        """PRD §6.2.1: immediate coach alert push+SMS; class-1 bypasses queue ordering —
        recorders must already hold the sends when handle() returns."""
        row = service.handle(
            sentinel(1),
            student_id="stu-1",
            assigned_coach=ASSIGNED,
            oncall=[ONCALL_A, ONCALL_INACTIVE, ONCALL_B],
        )
        push_recipients = [to for to, _ in deps["push"].sent]
        sms_recipients = [to for to, _ in deps["sms"].sent]
        assert push_recipients == ["coach-assigned", "coach-oncall-a", "coach-oncall-b"]
        assert sms_recipients == ["+15550000001", "+15550000002", "+15550000003"]
        for _, note in deps["push"].sent + deps["sms"].sent:
            assert note.deep_link == f"/coach/escalations/{row.id}"  # 02-UIUX §5

    def test_assigned_coach_in_oncall_rotation_is_not_double_alerted(self, service, deps):
        service.handle(
            sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[ASSIGNED, ONCALL_A]
        )
        push_recipients = [to for to, _ in deps["push"].sent]
        assert push_recipients == ["coach-assigned", "coach-oncall-a"]

    def test_audit_rows_on_create_and_each_alert(self, service, deps):
        service.handle(
            sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[ONCALL_A]
        )
        actions = [r["action"] for r in deps["audit"].rows]
        assert actions == [
            "escalation_created:class-1",
            "class1_alert_sent:coach-assigned",
            "class1_alert_sent:coach-oncall-a",
        ]
        assert all(r["student_id"] == "stu-1" for r in deps["audit"].rows)

    def test_coach_escalation_alerts_never_pass_through_budgets(self, service, deps):
        """02-UIUX §0.3: coach escalations are exempt. The service sends directly and
        an exhausted budget provably would not block the coach escalation channel."""
        budget = NotificationBudget(
            budgets={"coach": BudgetRule(limit=0, period="day")}, clock=deps["clock"]
        )
        service.handle(sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        assert len(deps["push"].sent) == 1 and len(deps["sms"].sent) == 1
        assert budget.check_and_count("coach", "stu-1", channel=COACH_ESCALATION_CHANNEL) is True


class FailingSender:
    """Test fake: records attempts like the Recorders, then raises (provider outage)."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.attempted: list[str] = []
        self.sent: list[tuple[str, object]] = []
        self.fail_for = fail_for  # None → fail every send

    def send(self, *, to: str, notification) -> None:
        self.attempted.append(to)
        if self.fail_for is None or to in self.fail_for:
            raise RuntimeError(f"provider 500 for {to}")
        self.sent.append((to, notification))


class FailingAuditWriter:
    """Test fake: audit sink outage — every write raises."""

    def write(self, **kwargs) -> None:
        raise RuntimeError("audit store unavailable")


class TestClass1FailureIsolation:
    """PRD §6.2 class-1 is the safety path: every channel to every recipient must be
    attempted even when senders/audit fail, with an aggregate error raised after."""

    ONCALL = [ONCALL_A, ONCALL_B]
    ALL_PUSH = ["coach-assigned", "coach-oncall-a", "coach-oncall-b"]
    ALL_SMS = ["+15550000001", "+15550000002", "+15550000003"]

    def test_push_outage_still_sends_sms_to_every_coach_then_raises(self, deps):
        push = FailingSender()  # OneSignal down for everyone
        service = EscalationService(
            deps["store"], push, deps["sms"], deps["audit"], clock=deps["clock"]
        )
        with pytest.raises(ExceptionGroup) as excinfo:
            service.handle(
                sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=self.ONCALL
            )
        # SMS is the redundant channel for exactly this outage: all coaches texted.
        assert [to for to, _ in deps["sms"].sent] == self.ALL_SMS
        assert push.attempted == self.ALL_PUSH  # every push was still attempted
        assert len(excinfo.value.exceptions) == 3  # nothing swallowed
        # SMS got through, so each coach's alert audits as sent.
        actions = [r["action"] for r in deps["audit"].rows]
        assert [a for a in actions if a.startswith("class1_alert_sent")] == [
            f"class1_alert_sent:{c}" for c in self.ALL_PUSH
        ]

    def test_sms_outage_still_pushes_to_every_coach_then_raises(self, deps):
        sms = FailingSender()  # Twilio down for everyone
        service = EscalationService(
            deps["store"], deps["push"], sms, deps["audit"], clock=deps["clock"]
        )
        with pytest.raises(ExceptionGroup):
            service.handle(
                sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=self.ONCALL
            )
        assert [to for to, _ in deps["push"].sent] == self.ALL_PUSH
        assert sms.attempted == self.ALL_SMS

    def test_one_unreachable_coach_does_not_abort_the_rest_of_the_fanout(self, deps):
        push = FailingSender(fail_for={"coach-assigned"})  # only the first recipient fails
        service = EscalationService(
            deps["store"], push, deps["sms"], deps["audit"], clock=deps["clock"]
        )
        with pytest.raises(ExceptionGroup):
            service.handle(
                sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=self.ONCALL
            )
        assert push.attempted == self.ALL_PUSH
        assert [to for to, _ in push.sent] == ["coach-oncall-a", "coach-oncall-b"]
        # The assigned coach still got the redundant SMS despite their push failing.
        assert [to for to, _ in deps["sms"].sent] == self.ALL_SMS

    def test_audit_outage_does_not_suppress_class1_alerts(self, deps):
        service = EscalationService(
            deps["store"], deps["push"], deps["sms"], FailingAuditWriter(), clock=deps["clock"]
        )
        with pytest.raises(ExceptionGroup) as excinfo:
            service.handle(
                sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=self.ONCALL
            )
        # All alerts fired even though every audit write raised.
        assert [to for to, _ in deps["push"].sent] == self.ALL_PUSH
        assert [to for to, _ in deps["sms"].sent] == self.ALL_SMS
        # created + one per coach = 4 audit failures surfaced, none swallowed.
        assert len(excinfo.value.exceptions) == 4

    def test_both_channels_down_audits_alert_failed_per_coach(self, deps):
        service = EscalationService(
            deps["store"], FailingSender(), FailingSender(), deps["audit"], clock=deps["clock"]
        )
        with pytest.raises(ExceptionGroup):
            service.handle(sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        assert [r["action"] for r in deps["audit"].rows] == [
            "escalation_created:class-1",
            "class1_alert_failed:coach-assigned",
        ]

    def test_total_outage_row_still_created_and_phone_tree_backstop_armed(self, deps, clock):
        """Worst case: push, SMS, and audit all down. The row exists with its 15-min
        SLA, so the phone-tree backstop still fires on the next overdue sweep."""
        service = EscalationService(
            deps["store"], FailingSender(), FailingSender(), FailingAuditWriter(),
            phone_tree=deps["phone_tree"], clock=clock,
        )
        with pytest.raises(ExceptionGroup):
            service.handle(sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        (row,) = deps["store"].rows.values()
        assert row.sla_due == T0 + timedelta(minutes=15)
        clock.advance(timedelta(minutes=16))
        recovered = EscalationService(
            deps["store"], deps["push"], deps["sms"], deps["audit"],
            phone_tree=deps["phone_tree"], clock=clock,
        )
        assert [e.id for e in recovered.check_overdue()] == [row.id]
        assert deps["phone_tree"].fired == [row.id]

    def test_non_class1_audit_failure_still_propagates(self, deps):
        """Failure tolerance is scoped to the class-1 safety path only — audit
        writes for other classes keep their normal fail-loud behavior."""
        service = EscalationService(
            deps["store"], deps["push"], deps["sms"], FailingAuditWriter(), clock=deps["clock"]
        )
        with pytest.raises(RuntimeError, match="audit store unavailable"):
            service.handle(sentinel(2), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])


class TestOtherClasses:
    @pytest.mark.parametrize(
        ("cls", "sla"),
        [(2, timedelta(hours=24)), (3, timedelta(hours=48)), (4, None), (5, None)],
    )
    def test_sla_by_class_and_no_alert_fanout(self, service, deps, cls, sla):
        row = service.handle(
            sentinel(cls), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[ONCALL_A]
        )
        assert row.sla_due == (T0 + sla if sla is not None else None)
        assert deps["push"].sent == []
        assert deps["sms"].sent == []
        assert [r["action"] for r in deps["audit"].rows] == [f"escalation_created:class-{cls}"]

    def test_class_5_logs_for_engineering_visibility(self, service, caplog):
        with caplog.at_level(logging.WARNING, logger="spykt.workers.escalation"):
            row = service.handle(
                sentinel(5), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[]
            )
        assert any("class-5" in rec.message and row.id in rec.getMessage() for rec in caplog.records)

    def test_invalid_class_rejected(self, service):
        class Bogus:
            escalation_class = 7
            severity = "x"
            evidence_ref = "y"
            recommended_action = "z"

        with pytest.raises(ValueError, match="1-5"):
            service.handle(Bogus(), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])


class TestAckResolve:
    def test_acknowledge_stamps_at_and_by_and_audits(self, service, deps, clock):
        row = service.handle(sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        clock.advance(timedelta(minutes=5))
        acked = service.acknowledge(row.id, "coach-assigned")
        assert acked.acknowledged_at == T0 + timedelta(minutes=5)
        assert acked.acknowledged_by == "coach-assigned"
        last = deps["audit"].rows[-1]
        assert last["action"] == "escalation_acknowledged"
        assert last["human_approver"] == "coach-assigned"

    def test_resolve_stamps_resolved_at_and_audits(self, service, deps, clock):
        row = service.handle(sentinel(2), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        clock.advance(timedelta(hours=1))
        resolved = service.resolve(row.id, "coach-assigned")
        assert resolved.resolved_at == T0 + timedelta(hours=1)
        last = deps["audit"].rows[-1]
        assert last["action"] == "escalation_resolved"
        assert last["human_approver"] == "coach-assigned"


class TestOverduePhoneTree:
    def test_unacknowledged_class1_past_sla_fires_phone_tree_exactly_once(
        self, service, deps, clock
    ):
        row = service.handle(sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        clock.advance(timedelta(minutes=16))
        fired = service.check_overdue()
        assert [e.id for e in fired] == [row.id]
        assert deps["phone_tree"].fired == [row.id]
        # Idempotent: repeated sweeps never re-fire (GAP-08 "once per escalation").
        assert service.check_overdue() == []
        assert service.check_overdue(clock() + timedelta(hours=2)) == []
        assert deps["phone_tree"].fired == [row.id]
        assert [r["action"] for r in deps["audit"].rows].count("phone_tree_fired") == 1

    def test_acknowledged_class1_does_not_fire(self, service, deps, clock):
        row = service.handle(sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        service.acknowledge(row.id, "coach-assigned")
        clock.advance(timedelta(minutes=30))
        assert service.check_overdue() == []
        assert deps["phone_tree"].fired == []

    def test_not_yet_due_class1_does_not_fire(self, service, deps, clock):
        service.handle(sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        clock.advance(timedelta(minutes=10))
        assert service.check_overdue() == []
        assert deps["phone_tree"].fired == []

    def test_overdue_class2_is_not_phone_treed(self, service, deps, clock):
        """The 15-min → admin phone tree is a class-1 (wellbeing) protocol (GAP-08)."""
        service.handle(sentinel(2), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        clock.advance(timedelta(hours=48))
        assert service.check_overdue() == []
        assert deps["phone_tree"].fired == []

    def test_missing_hook_logs_loudly_and_row_stays_eligible(self, deps, clock, caplog):
        unwired = EscalationService(
            deps["store"], deps["push"], deps["sms"], deps["audit"], clock=clock
        )
        row = unwired.handle(sentinel(1), student_id="stu-1", assigned_coach=ASSIGNED, oncall=[])
        clock.advance(timedelta(minutes=20))
        with caplog.at_level(logging.ERROR, logger="spykt.workers.escalation"):
            assert unwired.check_overdue() == []
        assert any("phone-tree hook" in rec.message for rec in caplog.records)
        # Once a hook is wired (same store), the escalation still fires — not lost.
        assert service_fires(deps, clock, row.id)


def service_fires(deps, clock, expected_id: str) -> bool:
    tree = RecordingPhoneTree()
    wired = EscalationService(
        deps["store"], deps["push"], deps["sms"], deps["audit"], phone_tree=tree, clock=clock
    )
    wired.check_overdue()
    return tree.fired == [expected_id]


# ---------------------------------------------------------------------------
# PostgresEscalationStore integration (gated on DATABASE_URL, like packages/audit)
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "infra" / "supabase" / "migrations"


@pytest.fixture(scope="module")
def db():
    """Fresh schema: drop + re-apply migrations; seed student + coaches + oncall."""
    import psycopg

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("drop schema if exists public cascade; create schema public;")
        cur.execute("drop schema if exists app cascade;")
        cur.execute("grant all on schema public to public;")
        for mig in sorted(MIGRATIONS_DIR.glob("*.sql")):
            cur.execute(mig.read_text())

        fam, stu = str(uuid.uuid4()), str(uuid.uuid4())
        coach_a, coach_b = str(uuid.uuid4()), str(uuid.uuid4())
        cur.execute("insert into families (id) values (%s)", (fam,))
        cur.execute(
            "insert into students (id, clerk_id, family_id, grade)"
            " values (%s, 'clerk_esc', %s, 10)",
            (stu, fam),
        )
        cur.execute("insert into coaches (id, clerk_id) values (%s, 'clerk_coach_a')", (coach_a,))
        cur.execute("insert into coaches (id, clerk_id) values (%s, 'clerk_coach_b')", (coach_b,))
        cur.execute(
            "insert into oncall (coach_id, priority, active) values (%s, 2, true)", (coach_b,)
        )
        cur.execute(
            "insert into oncall (coach_id, priority, active) values (%s, 1, false)", (coach_a,)
        )
    yield {"student_id": stu, "coach_a": coach_a, "coach_b": coach_b}
    conn.close()


@pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set (escalation store integration needs Postgres)"
)
class TestPostgresEscalationStore:
    @pytest.fixture()
    def store(self, db):
        """Store on a service-plane connection (workers run as service_role)."""
        import psycopg

        conn = psycopg.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute("set role service_role")
        yield PostgresEscalationStore(conn)
        conn.close()

    def _create(self, store, db, *, cls=1, sla_offset=timedelta(minutes=15)):
        return store.create(
            student_id=db["student_id"],
            escalation_class=cls,
            severity="high",
            payload={"evidence_ref": "transcript:abc#42", "recommended_action": "outreach"},
            assigned_coach=db["coach_a"],
            sla_due=T0 + sla_offset,
            created_at=T0,
        )

    def test_create_and_read_back(self, store, db):
        row = self._create(store, db)
        assert row.escalation_class == 1
        assert row.student_id == db["student_id"]
        assert row.assigned_coach == db["coach_a"]
        assert row.sla_due == T0 + timedelta(minutes=15)
        assert row.payload["evidence_ref"] == "transcript:abc#42"
        assert row.acknowledged_at is None and row.resolved_at is None

    def test_ack_stamps_acknowledged_at_and_by(self, store, db):
        row = self._create(store, db)
        acked = store.ack(row.id, db["coach_b"], T0 + timedelta(minutes=5))
        assert acked.acknowledged_at == T0 + timedelta(minutes=5)
        assert acked.acknowledged_by == db["coach_b"]

    def test_resolve_stamps_resolved_at(self, store, db):
        row = self._create(store, db)
        resolved = store.resolve(row.id, db["coach_a"], T0 + timedelta(hours=1))
        assert resolved.resolved_at == T0 + timedelta(hours=1)

    def test_list_overdue_and_phone_tree_marker(self, store, db):
        overdue_row = self._create(store, db)
        acked_row = self._create(store, db)
        store.ack(acked_row.id, db["coach_a"], T0 + timedelta(minutes=1))
        self._create(store, db, cls=2, sla_offset=timedelta(hours=24))  # class-2: not phone-treed

        now = T0 + timedelta(minutes=16)
        overdue_ids = [row.id for row in store.list_overdue(now)]
        assert overdue_row.id in overdue_ids
        assert acked_row.id not in overdue_ids

        store.mark_phone_tree_fired(overdue_row.id, now)
        refetched = {row.id: row for row in store.list_overdue(now)}[overdue_row.id]
        assert refetched.phone_tree_fired_at == now  # marker persists in payload jsonb

    def test_list_active_oncall_orders_by_priority(self, store, db):
        assert store.list_active_oncall() == [db["coach_b"]]  # coach_a is inactive

    def test_unknown_id_raises(self, store):
        with pytest.raises(KeyError):
            store.ack(str(uuid.uuid4()), str(uuid.uuid4()), T0)
