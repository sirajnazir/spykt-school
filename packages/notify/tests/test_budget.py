"""Notification budget tests (02-UIUX §0.3: ≤2 push/day student, ≤1/week parent;
coach escalations exempt — that exemption is safety-relevant per PRD §6.2)."""

from datetime import UTC, datetime, timedelta

from spykt_notify import (
    COACH_ESCALATION_CHANNEL,
    BudgetRule,
    NotificationBudget,
)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


T0 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)  # a Monday


def test_student_default_two_pushes_per_day():
    budget = NotificationBudget(clock=FixedClock(T0))
    assert budget.check_and_count("student", "s1") is True
    assert budget.check_and_count("student", "s1") is True
    assert budget.check_and_count("student", "s1") is False  # 3rd of the day blocked


def test_student_budget_resets_next_day():
    clock = FixedClock(T0)
    budget = NotificationBudget(clock=clock)
    assert budget.check_and_count("student", "s1")
    assert budget.check_and_count("student", "s1")
    assert not budget.check_and_count("student", "s1")
    clock.advance(timedelta(days=1))
    assert budget.check_and_count("student", "s1") is True


def test_parent_default_one_push_per_week():
    clock = FixedClock(T0)
    budget = NotificationBudget(clock=clock)
    assert budget.check_and_count("parent", "s1") is True
    clock.advance(timedelta(days=3))  # same ISO week
    assert budget.check_and_count("parent", "s1") is False
    clock.advance(timedelta(days=7))  # next ISO week
    assert budget.check_and_count("parent", "s1") is True


def test_budgets_are_per_student():
    budget = NotificationBudget(clock=FixedClock(T0))
    assert budget.check_and_count("student", "s1")
    assert budget.check_and_count("student", "s1")
    assert not budget.check_and_count("student", "s1")
    # A different student's budget is untouched.
    assert budget.check_and_count("student", "s2") is True


def test_constructor_config_overrides_defaults():
    budget = NotificationBudget(
        budgets={"student": BudgetRule(limit=1, period="day")}, clock=FixedClock(T0)
    )
    assert budget.check_and_count("student", "s1") is True
    assert budget.check_and_count("student", "s1") is False


def test_coach_escalation_channel_is_exempt():
    """PRD §6.2 / 02-UIUX §0.3: escalation delivery to coaches must NEVER be throttled,
    even under a maximally restrictive budget config."""
    budget = NotificationBudget(
        budgets={"coach": BudgetRule(limit=0, period="day")}, clock=FixedClock(T0)
    )
    for _ in range(50):
        assert budget.check_and_count("coach", "s1", channel=COACH_ESCALATION_CHANNEL) is True


def test_coach_escalation_exemption_does_not_consume_other_budgets():
    budget = NotificationBudget(clock=FixedClock(T0))
    for _ in range(10):
        assert budget.check_and_count("student", "s1", channel=COACH_ESCALATION_CHANNEL)
    # Regular student pushes still have their full budget.
    assert budget.check_and_count("student", "s1") is True
    assert budget.check_and_count("student", "s1") is True
    assert budget.check_and_count("student", "s1") is False


def test_role_without_configured_budget_is_denied():
    """Fail closed (CLAUDE.md): an unknown/unbudgeted role must not default to
    unlimited sends — allowing it requires an explicit opt-in BudgetRule."""
    budget = NotificationBudget(clock=FixedClock(T0))
    assert budget.check_and_count("admin", "s1") is False
    assert budget.check_and_count("tpyo-role", "s1") is False


def test_coach_non_escalation_sends_are_denied_by_default():
    """02-UIUX §0.3: "escalations only for coach" — the exemption covers the coach
    escalation channel ONLY; ordinary coach pushes are blocked, not unlimited."""
    budget = NotificationBudget(clock=FixedClock(T0))
    assert budget.check_and_count("coach", "s1") is False
    assert budget.check_and_count("coach", "s1", channel="push") is False
    # The escalation channel stays exempt (PRD §6.2 — never throttled).
    assert budget.check_and_count("coach", "s1", channel=COACH_ESCALATION_CHANNEL) is True


def test_explicit_opt_in_rule_allows_an_otherwise_unbudgeted_role():
    budget = NotificationBudget(
        budgets={"admin": BudgetRule(limit=1, period="day")}, clock=FixedClock(T0)
    )
    assert budget.check_and_count("admin", "s1") is True
    assert budget.check_and_count("admin", "s1") is False  # rule enforced, not unlimited
