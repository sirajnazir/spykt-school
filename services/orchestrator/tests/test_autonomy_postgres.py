"""PostgresConsentStore integration test (approvals table, 0002_safety_spine.sql).

Runs against any local/CI Postgres (same pattern as packages/audit
tests/test_postgres_writer.py): fresh schema, migrations applied, rows written as
service_role. Requires DATABASE_URL (superuser); skipped when unset.
"""

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from spykt_audit import PostgresAuditWriter

from spykt_orchestrator.autonomy import PostgresConsentStore, authorize

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set (consent store integration needs Postgres)"
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "infra" / "supabase" / "migrations"

INSERT_APPROVAL = (
    "insert into approvals"
    " (student_id, subject_type, subject_id, level, approver_role, approver_clerk_id, decision)"
    " values (%s, %s, %s, %s, %s, %s, %s)"
)


@pytest.fixture(scope="module")
def db():
    """Fresh schema per run: drop + re-apply migrations, seed one student for the FK."""
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("drop schema if exists public cascade; create schema public;")
        cur.execute("drop schema if exists app cascade;")
        cur.execute("grant all on schema public to public;")
        for mig in sorted(MIGRATIONS_DIR.glob("*.sql")):
            cur.execute(mig.read_text())

        fam, stu = str(uuid.uuid4()), str(uuid.uuid4())
        cur.execute("insert into families (id) values (%s)", (fam,))
        cur.execute(
            "insert into students (id, clerk_id, family_id, grade) values (%s, 'clerk_auto', %s, 10)",
            (stu, fam),
        )
    yield {"student_id": stu}
    conn.close()


@pytest.fixture()
def service_conn(db):
    """Service-plane connection (workers read approvals / write audit as service_role)."""
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("set role service_role")
    yield conn
    conn.close()


def test_l2_blocked_then_allowed_once_coach_approval_row_exists(service_conn, db):
    student = db["student_id"]
    subject = str(uuid.uuid4())
    store = PostgresConsentStore(service_conn)
    audit = PostgresAuditWriter(service_conn)

    blocked = authorize("quarter_roadmap_change", student, subject, store, audit)
    assert blocked.allowed is False
    assert blocked.required_level == "L2"

    with service_conn.cursor() as cur:
        cur.execute(
            INSERT_APPROVAL,
            (student, "quarter_roadmap_change", subject, "L2", "coach", "clerk_coach_pg", "approved"),
        )

    allowed = authorize("quarter_roadmap_change", student, subject, store, audit)
    assert allowed.allowed is True

    with service_conn.cursor() as cur:
        cur.execute(
            "select action, autonomy_level from audit_log"
            " where student_id = %s and action like 'autonomy_%%' order by ts",
            (student,),
        )
        rows = cur.fetchall()
    assert ("autonomy_block", "L2") in rows
    assert ("autonomy_allow", "L2") in rows


def test_l3_dual_signoff_enforced_against_real_rows(service_conn, db):
    student = db["student_id"]
    subject = str(uuid.uuid4())
    store = PostgresConsentStore(service_conn)
    audit = PostgresAuditWriter(service_conn)

    with service_conn.cursor() as cur:
        cur.execute(
            INSERT_APPROVAL,
            (student, "fee_bearing_application", subject, "L3", "coach", "clerk_coach_pg", "approved"),
        )
    assert authorize("fee_bearing_application", student, subject, store, audit).allowed is False

    with service_conn.cursor() as cur:
        cur.execute(
            INSERT_APPROVAL,
            (student, "fee_bearing_application", subject, "L3", "parent", "clerk_parent_pg", "approved"),
        )
    assert authorize("fee_bearing_application", student, subject, store, audit).allowed is True


def test_rejected_and_cross_subject_rows_do_not_count(service_conn, db):
    student = db["student_id"]
    subject, other = str(uuid.uuid4()), str(uuid.uuid4())
    store = PostgresConsentStore(service_conn)
    audit = PostgresAuditWriter(service_conn)

    with service_conn.cursor() as cur:
        cur.execute(
            INSERT_APPROVAL,
            (student, "spike_thesis_pivot", subject, "L2", "coach", "clerk_coach_pg", "rejected"),
        )
        cur.execute(
            INSERT_APPROVAL,
            (student, "spike_thesis_pivot", other, "L2", "coach", "clerk_coach_pg", "approved"),
        )
    assert authorize("spike_thesis_pivot", student, subject, store, audit).allowed is False


def test_conninfo_string_constructor_reads(db):
    store = PostgresConsentStore(DATABASE_URL)
    try:
        assert store.approvals_for(db["student_id"], "weekly_plan", str(uuid.uuid4())) == []
    finally:
        store.close()
