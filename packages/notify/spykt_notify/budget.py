"""Per-role notification budgets (02-UIUX §0.3 / §5).

Budget: ≤2 push/day per student, ≤1/week per parent; "escalations only for coach" —
the coach escalation channel is EXEMPT from budgets by construction (PRD §6.2 class-1
alerts must never be throttled; weakening this would be a safety regression).

Everything else fails CLOSED: a role with no configured budget rule is denied.
02-UIUX §0.3 grants coaches escalations ONLY, so non-escalation coach sends are
blocked by default, as is any unknown (or typo'd) role. Allowing an unbudgeted
role requires an explicit opt-in BudgetRule in constructor config.

Budgets are constructor config, not env. Windows are calendar-based (UTC day / ISO
week) — boring and observable, matching how "≤2 push/day" reads.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Literal, Mapping

logger = logging.getLogger("spykt.notify.budget")

# Channel name for coach escalation alerts (PRD §6.2). Always exempt from budgets.
COACH_ESCALATION_CHANNEL = "coach_escalation"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class BudgetRule:
    """Max `limit` pushes per calendar `period` for one (role, student) pair."""

    limit: int
    period: Literal["day", "week"]


# 02-UIUX §0.3 defaults: ≤2 push/day student, ≤1/week parent. Coach deliberately has
# NO budget row: "escalations only for coach" means coach escalation delivery is exempt
# (see check_and_count) and every OTHER coach send is denied by the fail-closed default.
DEFAULT_BUDGETS: Mapping[str, BudgetRule] = {
    "student": BudgetRule(limit=2, period="day"),
    "parent": BudgetRule(limit=1, period="week"),
}


def _window_key(rule: BudgetRule, now: datetime) -> str:
    if rule.period == "day":
        return now.date().isoformat()
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class NotificationBudget:
    """Counts sends per (role, student) per window; `check_and_count` gates callers.

    Callers ask permission BEFORE sending: a True return has already consumed one
    unit of budget. The coach escalation channel bypasses counting and blocking
    entirely (02-UIUX §0.3: "escalations only for coach"; PRD §6.2 class-1 alerts
    are unconditional). Every other send from a role without a configured rule is
    DENIED — compliance ambiguity fails closed.
    """

    def __init__(
        self,
        budgets: Mapping[str, BudgetRule] | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._budgets: Mapping[str, BudgetRule] = dict(DEFAULT_BUDGETS if budgets is None else budgets)
        self._clock = clock
        # (role, student_id, window_key) -> count
        self._counts: dict[tuple[str, str, str], int] = defaultdict(int)

    def check_and_count(self, role: str, student_id: str, *, channel: str = "push") -> bool:
        """Return True (and consume budget) if a send is allowed; False if over budget
        or the role has no configured budget (fail closed).

        `channel == COACH_ESCALATION_CHANNEL` is always allowed and never counted:
        coach escalation delivery (PRD §6.2) must not be throttled by engagement
        budgets — this exemption is deliberate and safety-relevant.
        """
        if channel == COACH_ESCALATION_CHANNEL:
            return True
        rule = self._budgets.get(role)
        if rule is None:
            # Fail CLOSED: 02-UIUX §0.3 grants coaches "escalations only", so a
            # non-escalation coach send — or any role without an explicit opt-in
            # BudgetRule — is denied rather than defaulting to unlimited.
            logger.warning(
                "notification denied: role %r has no configured budget"
                " (channel=%s, student=%s); unbudgeted roles fail closed (02-UIUX §0.3)",
                role,
                channel,
                student_id,
            )
            return False
        key = (role, student_id, _window_key(rule, self._clock()))
        if self._counts[key] >= rule.limit:
            return False
        self._counts[key] += 1
        return True
