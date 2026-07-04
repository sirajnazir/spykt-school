"""PostgresAuditWriter integration test (01-TECH_SPEC §3 audit_log, §10 append-only).

Runs against any Postgres with pgvector (CI service container or `make db-up` locally),
never against a real project. Applies infra/supabase/migrations/*.sql on a fresh schema
(same pattern as infra/supabase/tests/test_rls_smoke.py), writes as service_role, and
asserts UPDATE/DELETE on audit_log are impossible even for service_role.

Requires DATABASE_URL (superuser). Skipped when unset so `uv run pytest` stays green
without a database.
"""

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from spykt_audit import PostgresAuditWriter

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set (audit writer integration needs Postgres)"
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "infra" / "supabase" / "migrations"

SELECT_COLUMNS = "agent, model, prompt_version, action, autonomy_level, human_approver, student_id"


@pytest.fixture(scope="module")
def db():
    """Fresh schema per test run: drop + re-apply migrations, seed one student for the FK."""
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
            "insert into students (id, clerk_id, family_id, grade) values (%s, 'clerk_audit', %s, 10)",
            (stu, fam),
        )
    yield {"student_id": stu}
    conn.close()


@pytest.fixture()
def service_conn(db):
    """Connection acting as the service plane (workers write audit rows as service_role)."""
    conn = psycopg.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("set role service_role")
    yield conn
    conn.close()


def test_write_as_service_role_and_read_back(service_conn, db):
    writer = PostgresAuditWriter(service_conn)
    writer.write(
        agent="pathway_planner",
        model="claude-fable-5",
        prompt_version="pp-v3",
        action="plan_proposed",
        autonomy_level="L2",
        human_approver="coach_7",
        student_id=db["student_id"],
    )
    with service_conn.cursor() as cur:
        cur.execute(f"select {SELECT_COLUMNS} from audit_log where action = 'plan_proposed'")
        rows = cur.fetchall()
    assert rows == [
        (
            "pathway_planner",
            "claude-fable-5",
            "pp-v3",
            "plan_proposed",
            "L2",
            "coach_7",
            uuid.UUID(db["student_id"]),
        )
    ]


def test_id_and_ts_are_db_generated(service_conn, db):
    PostgresAuditWriter(service_conn).write(agent="sentinel", action="escalation_raised")
    with service_conn.cursor() as cur:
        cur.execute("select id, ts from audit_log where action = 'escalation_raised'")
        (row_id, ts) = cur.fetchone()
    assert row_id is not None
    assert ts is not None


def test_conninfo_string_constructor_writes(db):
    writer = PostgresAuditWriter(DATABASE_URL)
    try:
        writer.write(agent="zuzu", action="conninfo_write")
    finally:
        writer.close()
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from audit_log where action = 'conninfo_write'")
        assert cur.fetchone()[0] == 1


def test_update_and_delete_blocked_for_service_role(db):
    """Append-only at the grant level (01-TECH_SPEC §10): even service_role cannot mutate."""
    conn = psycopg.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("set role service_role")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("update audit_log set action = 'tampered'")
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("set role service_role")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("delete from audit_log")
    finally:
        conn.close()
