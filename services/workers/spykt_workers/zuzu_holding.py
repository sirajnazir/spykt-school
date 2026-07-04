"""Zuzu holding-pattern behavior (class-1 wellbeing escalation).

PRD §6.2.1: on a class-1 wellbeing signal "Zuzu shifts to supportive holding
pattern (no coaching pressure), never handles it alone."
02-UIUX §2.3 boundary behavior: the "I've looped in Coach <name>, she'll reach
out today" message appears ONLY after Sentinel fires — never fake it.
PRD §3 role boundary: Zuzu is not a therapist; no diagnosis language.

Enforcement seams (prime directive 1 — do not weaken):
- A HoldingPattern can only exist for a real, fired escalation:
  `enter_holding_pattern` (and HoldingPattern.__post_init__ itself) raises
  HoldingPatternError unless escalation_id is truthy and fired_alert is True.
- `coach_looped_in_message` is the ONLY code path that emits the looped-in
  message, and it requires an active HoldingPattern — which by construction
  means Sentinel fired.
- The only exit path is a coach resolution (`exit_holding_pattern` with a
  coach id); Zuzu never self-resolves a class-1 event.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from spykt_workers.holding_copy import HOLDING_COPY

# Coaching-pressure keys stripped from Zuzu's pinned context while holding.
SUPPRESSED_CONTEXT_KEYS = frozenset({"plan", "tasks", "commitments", "streak", "streaks", "deadlines"})


class HoldingPatternError(RuntimeError):
    """Raised on any attempt to fake, bypass, or improperly exit a holding pattern."""


@dataclass
class HoldingPattern:
    """Supportive holding state for one student, keyed by student_id.

    Construct ONLY via enter_holding_pattern(); __post_init__ re-validates so a
    direct construction cannot dodge the Sentinel-fired requirement either.
    """

    student_id: str
    escalation_id: str
    fired_alert: bool
    entered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    active: bool = True
    resolved_by_coach_id: str | None = None

    def __post_init__(self) -> None:
        if not self.student_id:
            raise HoldingPatternError("Holding pattern requires a student_id.")
        if not self.escalation_id or self.fired_alert is not True:
            raise HoldingPatternError(
                "Refusing to enter holding pattern without a fired Sentinel escalation "
                "(02-UIUX §2.3: never fake it). Got "
                f"escalation_id={self.escalation_id!r}, fired_alert={self.fired_alert!r}."
            )


def enter_holding_pattern(escalation_id: str, student_id: str, fired_alert: bool) -> HoldingPattern:
    """Enter the supportive holding pattern for a student.

    Raises HoldingPatternError unless `escalation_id` is truthy and
    `fired_alert` is True — no escalation, no holding pattern, no
    coach-looped-in message.
    """
    if not escalation_id or fired_alert is not True:
        raise HoldingPatternError(
            "Refusing to enter holding pattern without a fired Sentinel escalation "
            "(02-UIUX §2.3: never fake it). Got "
            f"escalation_id={escalation_id!r}, fired_alert={fired_alert!r}."
        )
    return HoldingPattern(student_id=student_id, escalation_id=escalation_id, fired_alert=fired_alert)


def _require_active_pattern(pattern: object) -> HoldingPattern:
    if not isinstance(pattern, HoldingPattern):
        raise HoldingPatternError("A HoldingPattern (created via enter_holding_pattern) is required.")
    if not pattern.active:
        raise HoldingPatternError(
            f"Holding pattern for student {pattern.student_id!r} was already resolved."
        )
    return pattern


def build_holding_system_fragment(pattern: HoldingPattern) -> str:
    """System-prompt fragment shifting Zuzu into the supportive holding tone.

    Includes the static human-approved copy (GAP-08) and explicitly instructs
    NO plan/task/deadline pressure and NO new commitments (PRD §6.2.1).
    """
    pattern = _require_active_pattern(pattern)
    return (
        "HOLDING PATTERN ACTIVE (class-1 wellbeing escalation "
        f"{pattern.escalation_id}). A human coach has been alerted and owns "
        "this; you never handle it alone.\n"
        "Tone: warm, supportive, present. Listen. You are a coach, not a "
        "therapist: never diagnose, never assess, never label what the "
        "student is going through.\n"
        "Apply NO plan, task, or deadline pressure and request NO new "
        "commitments. Do not mention streaks, progress, or schoolwork unless "
        "the student raises it, and even then do not push.\n"
        "Share this static, human-approved copy with the student:\n"
        f"{HOLDING_COPY}"
    )


def coach_looped_in_message(pattern: HoldingPattern, coach_name: str) -> str:
    """The 02-UIUX §2.3 coach-looped-in message.

    Requires an active HoldingPattern, which by construction means Sentinel
    fired. There is deliberately NO other code path that emits this message —
    never fake it.
    """
    _require_active_pattern(pattern)
    if not coach_name:
        raise HoldingPatternError("coach_name is required for the looped-in message.")
    return f"I've looped in Coach {coach_name}, she'll reach out today."


def suppress_coaching_context(pinned_context: dict) -> dict:
    """Return a copy of `pinned_context` with coaching-pressure keys removed.

    Strips plan/tasks/commitments/streak-style keys and adds a
    'holding_pattern': True marker; Zuzu context assembly consumes this in
    Phase 3.
    """
    suppressed = {k: v for k, v in pinned_context.items() if k not in SUPPRESSED_CONTEXT_KEYS}
    suppressed["holding_pattern"] = True
    return suppressed


def exit_holding_pattern(pattern: HoldingPattern, resolved_by_coach_id: str) -> HoldingPattern:
    """Exit the holding pattern. The ONLY exit path is a coach resolution.

    Raises HoldingPatternError unless `resolved_by_coach_id` is a non-empty
    string; Zuzu never resolves a class-1 escalation itself.
    """
    pattern = _require_active_pattern(pattern)
    if not isinstance(resolved_by_coach_id, str) or not resolved_by_coach_id:
        raise HoldingPatternError(
            "Holding pattern can only be exited by a coach resolution: "
            f"resolved_by_coach_id must be a non-empty string, got {resolved_by_coach_id!r}."
        )
    pattern.active = False
    pattern.resolved_by_coach_id = resolved_by_coach_id
    return pattern
