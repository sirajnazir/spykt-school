"""Append-only audit log writers (01-TECH_SPEC §3 audit_log, §10; PRD §7.5).

Every agent action carries {agent, model, prompt_version, action, autonomy_level,
human_approver?, student_id?} into an append-only audit_log. Append-only is by
construction: this package exposes no UPDATE or DELETE path, matching the revoked
grants in infra/supabase/migrations/0001_core.sql
(`revoke update, delete on audit_log from authenticated, service_role`).

Both writers expose the exact same keyword-only `write(...)` signature; it is the
cross-package duck type other packages (e.g. spykt_anthropic_client) code against.
"""

from __future__ import annotations

import psycopg

_INSERT_SQL = (
    "INSERT INTO audit_log"
    " (agent, model, prompt_version, action, autonomy_level, human_approver, student_id)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


def _require(agent: str, action: str) -> None:
    """agent and action are the two non-nullable audit columns (01-TECH_SPEC §3)."""
    if not agent:
        raise ValueError("audit_log write requires a non-empty agent (PRD §7.5)")
    if not action:
        raise ValueError("audit_log write requires a non-empty action (PRD §7.5)")


class InMemoryAuditWriter:
    """Test double: records rows as dicts in `.rows`. Used by tests across the monorepo."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

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
    ) -> None:
        _require(agent, action)
        self.rows.append(
            {
                "agent": agent,
                "model": model,
                "prompt_version": prompt_version,
                "action": action,
                "autonomy_level": autonomy_level,
                "human_approver": human_approver,
                "student_id": student_id,
            }
        )


class PostgresAuditWriter:
    """INSERT-only writer against the audit_log table; id/ts are DB-generated.

    Accepts either a conninfo string (a connection is opened and owned by the writer)
    or an existing psycopg connection (caller keeps ownership, e.g. one already under
    `set role service_role`).
    """

    def __init__(self, conn: str | psycopg.Connection) -> None:
        if isinstance(conn, str):
            self._conn = psycopg.connect(conn)
            self._owns_conn = True
        else:
            self._conn = conn
            self._owns_conn = False

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
    ) -> None:
        _require(agent, action)
        with self._conn.cursor() as cur:
            cur.execute(
                _INSERT_SQL,
                (agent, model, prompt_version, action, autonomy_level, human_approver, student_id),
            )
        self._conn.commit()

    def close(self) -> None:
        """Close the connection if this writer opened it (no-op for caller-owned connections)."""
        if self._owns_conn:
            self._conn.close()
