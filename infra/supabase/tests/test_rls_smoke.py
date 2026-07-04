"""Phase 0 gate: RLS smoke test — a student cannot read another student's rows.

Runs against any Postgres with pgvector (CI service container or `make db-up` locally),
never against a real project. Applies infra/supabase/migrations/*.sql on a fresh
database, then exercises policies via SET ROLE authenticated + request.jwt.claims.

Requires DATABASE_URL (superuser). Skipped when unset so `uv run pytest` stays green
without a database.
"""

import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set (RLS smoke needs Postgres)")

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def claims(sub: str, role: str, family_id: str | None = None) -> str:
    c: dict = {"sub": sub, "role": role}
    if family_id:
        c["family_id"] = family_id
    return json.dumps(c)


@pytest.fixture(scope="module")
def db():
    """Fresh schema per test run: drop + re-apply migrations, seed two students."""
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("drop schema if exists public cascade; create schema public;")
        cur.execute("drop schema if exists app cascade;")
        cur.execute("grant all on schema public to public;")
        for mig in sorted(MIGRATIONS_DIR.glob("*.sql")):
            cur.execute(mig.read_text())

        fam_a, fam_b = str(uuid.uuid4()), str(uuid.uuid4())
        stu_a, stu_b = str(uuid.uuid4()), str(uuid.uuid4())
        cur.execute("insert into families (id) values (%s), (%s)", (fam_a, fam_b))
        cur.execute(
            "insert into students (id, clerk_id, family_id, grade) values (%s,'clerk_a',%s,10), (%s,'clerk_b',%s,11)",
            (stu_a, fam_a, stu_b, fam_b),
        )
        cur.execute(
            "insert into transcripts (student_id, role, content) values (%s,'student','A private words'), (%s,'student','B private words')",
            (stu_a, stu_b),
        )
        cur.execute(
            "insert into audit_log (agent, action, student_id) values ('test','seed',%s), ('test','seed',%s)",
            (stu_a, stu_b),
        )
        cur.execute("insert into pseudonym_map (student_id, pseudonym, salt) values (%s,'PSN-A','s1'), (%s,'PSN-B','s2')", (stu_a, stu_b))
    yield {"stu_a": stu_a, "stu_b": stu_b, "fam_a": fam_a}
    conn.close()


@pytest.fixture()
def as_student_a(db):
    """Connection acting as student A through the authenticated role."""
    conn = psycopg.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("set role authenticated")
        cur.execute("select set_config('request.jwt.claims', %s, false)", (claims("clerk_a", "student"),))
    yield conn
    conn.close()


def test_student_sees_only_self_in_students(as_student_a, db):
    with as_student_a.cursor() as cur:
        cur.execute("select clerk_id from students")
        rows = [r[0] for r in cur.fetchall()]
    assert rows == ["clerk_a"]


def test_cross_student_transcript_read_fails(as_student_a, db):
    """THE Phase 0 gate case: cross-student read returns nothing."""
    with as_student_a.cursor() as cur:
        cur.execute("select content from transcripts where student_id = %s", (db["stu_b"],))
        assert cur.fetchall() == []
        cur.execute("select content from transcripts where student_id = %s", (db["stu_a"],))
        assert len(cur.fetchall()) == 1


def test_cross_student_audit_read_fails(as_student_a, db):
    with as_student_a.cursor() as cur:
        cur.execute("select count(*) from audit_log")
        assert cur.fetchone()[0] == 1  # own row only


def test_pseudonym_map_is_service_only(as_student_a):
    with as_student_a.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("select * from pseudonym_map")


def test_audit_log_is_append_only_even_for_service_role(db):
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


def test_anonymous_claims_see_nothing(db):
    conn = psycopg.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("set role authenticated")
            cur.execute("select set_config('request.jwt.claims', '{}', false)")
            cur.execute("select count(*) from students")
            assert cur.fetchone()[0] == 0
            cur.execute("select count(*) from transcripts")
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()
