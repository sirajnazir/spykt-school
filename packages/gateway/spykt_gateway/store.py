"""Pseudonym store for the Pseudonymization Gateway (01-TECH_SPEC §7).

Holds the per-student stable pseudonym + salt, and the reversible token→original
mapping discovered at scrub time (NER/regex hits). The Postgres implementation
lands with integration wiring; the `pseudonym_map` table is service-role only
under RLS (PRD §7.1). `InMemoryPseudonymStore` backs unit tests and local runs.
"""

import secrets
from dataclasses import dataclass
from typing import Protocol


class TokenCollisionError(RuntimeError):
    """Raised when a token would silently remap to a different original.

    Tokens are HMAC-SHA256 truncated to 32 bits, so within one student two
    distinct originals can (rarely) derive the same token. Silently overwriting
    the reverse mapping would corrupt inbound `restore()` with no signal; per
    prime directive 3 the store refuses loudly instead.
    """


@dataclass(frozen=True)
class PseudonymRecord:
    """Stable per-student pseudonymization identity."""

    student_id: str
    pseudonym: str  # e.g. "Student-1a2b3c4d" — what Fable sees instead of the student's name
    salt: str  # hex; HMAC key for deterministic token derivation, unique per student


class PseudonymStore(Protocol):
    """Storage contract for pseudonym records and reversible token mappings."""

    def get(self, student_id: str) -> PseudonymRecord | None:
        """Return the student's record, or None if none exists yet."""
        ...

    def create(self, student_id: str) -> PseudonymRecord:
        """Create (or return the existing) record with a random pseudonym and salt."""
        ...

    def save_token_mapping(self, student_id: str, token: str, original: str) -> None:
        """Persist token→original so inbound text can be re-substituted (§7 Inbound).

        Implementations must raise TokenCollisionError if `token` already maps to a
        materially different original for this student (case-insensitive comparison)
        instead of silently overwriting. Case-only variants keep the first-seen
        surface form (token derivation lowercases, so case variants share one token).
        """
        ...

    def lookup_token(self, student_id: str, token: str) -> str | None:
        """Reverse a token for this student, or None if unknown."""
        ...


class InMemoryPseudonymStore:
    """Dict-backed PseudonymStore for tests and local development."""

    def __init__(self) -> None:
        self._records: dict[str, PseudonymRecord] = {}
        self._tokens: dict[tuple[str, str], str] = {}

    def get(self, student_id: str) -> PseudonymRecord | None:
        return self._records.get(student_id)

    def create(self, student_id: str) -> PseudonymRecord:
        existing = self._records.get(student_id)
        if existing is not None:
            return existing
        record = PseudonymRecord(
            student_id=student_id,
            pseudonym=f"Student-{secrets.token_hex(4)}",
            salt=secrets.token_hex(16),
        )
        self._records[student_id] = record
        return record

    def save_token_mapping(self, student_id: str, token: str, original: str) -> None:
        existing = self._tokens.get((student_id, token))
        if existing is not None:
            if existing.lower() != original.lower():
                raise TokenCollisionError(
                    f"Token {token} for student {student_id!r} already maps to a different original; "
                    "refusing to overwrite (32-bit truncation collision — restore() would corrupt silently)."
                )
            return  # First write wins: case variants canonicalize to the first-seen surface form.
        self._tokens[(student_id, token)] = original

    def lookup_token(self, student_id: str, token: str) -> str | None:
        return self._tokens.get((student_id, token))
