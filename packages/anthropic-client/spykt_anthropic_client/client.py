"""Shared Anthropic client with the four mandatory middlewares (01-TECH_SPEC §4.1).

Pipeline per call, in order:
  1. Resolve model from routing config (§4.3 / GAP-11).
  2. Budget guard (§4.1.3, PRD §10): pre-flight cost estimate vs. per-student monthly
     ceiling; over budget degrades to the fallback model with flag ``degraded_budget``.
  3. Retention gate (§4.1.4): the FINAL model chosen must pass ``require_pseudonymized``.
     Never bypassed, never caught-and-continued.
  4. Thinking config (§4.1.2): Fable models get summarized thinking display. Thinking
     blocks are passed through opaquely and NEVER parsed — machine-read output is the
     response body only.
  5. Refusal handling (§4.1.1, PRD §6.2.5): ``stop_reason == "refusal"`` writes a
     refusal audit action carrying the response's classifier identifier (§4.1.1:
     "log fable_refusal event with classifier id"), fires a class-5 escalation, and
     retries once on the fallback model (which itself passes steps 2-4) with flag
     ``model_fallback``. The audit action is ``fable_refusal`` when the refusing model
     is Fable; refusals from other models are audited as ``model_refusal`` (§4.1 scopes
     the ``fable_refusal`` event name to Fable; PRD §6.2.5 defines class 5 as Fable
     refusals) but still escalated — over-escalating is the safe direction. Because
     ``audit_log`` (owned by packages/audit) has no detail column, the classifier id is
     recorded as a colon suffix on the action: ``fable_refusal:<classifier_id>``.
  6. Audit (CLAUDE.md Phase 1 gate): an ``action="model_call"`` audit row is written for
     EVERY request issued, including the refusal retry.

ANTHROPIC_API_KEY is never required at import time: the real SDK client is constructed
lazily and only when no ``sdk_client`` was injected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from spykt_anthropic_client.config import ModelConfig
from spykt_anthropic_client.retention import FABLE_PREFIX, require_pseudonymized

logger = logging.getLogger(__name__)

# Coarse, observable pre-flight input estimate (01 §4.1.3): ~4 chars per token.
CHARS_PER_TOKEN = 4

# Fable thinking config (01 §4.1.2): summaries only; raw chain of thought never returned.
FABLE_THINKING_CONFIG: dict[str, str] = {"type": "adaptive", "display": "summarized"}


class AuditWriter(Protocol):
    """Duck-type of packages/audit's writer — depend on the Protocol, not the package."""

    def write(
        self,
        *,
        agent: str,
        model: str,
        prompt_version: str,
        action: str,
        autonomy_level: str | None = None,
        human_approver: str | None = None,
        student_id: str | None = None,
    ) -> None: ...


class SpendStore(Protocol):
    """Month-to-date model spend per student per model alias, USD."""

    def month_spend(self, student_id: str, model_alias: str) -> float: ...


class EscalationHook(Protocol):
    """Sentinel escalation callback (PRD §6.2); class 5 == model refusal."""

    def __call__(self, *, escalation_class: int, reason: str) -> None: ...


class BudgetExceededError(RuntimeError):
    """Raised when a job is over its budget ceiling and no fallback model exists."""


@dataclass
class CallResult:
    response: Any
    model_used: str
    flags: list[str]
    tokens_in: int
    tokens_out: int


def extract_refusal_classifier(response: Any) -> str | None:
    """Pull the refusal classifier identifier off a ``stop_reason=="refusal"`` response.

    01 §4.1.1: Fable refusals arrive as HTTP 200 with ``stop_reason: "refusal"`` and a
    classifier identifier. Checked attribute names cover the SDK spellings we accept
    from stubs and the live API; absent/blank ids return None (still audited, just
    without a suffix — never raises).
    """
    for attr in ("refusal_classifier", "classifier_id", "classifier"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def estimate_input_tokens(messages: list[dict[str, Any]], system: str | None) -> int:
    """Coarse pre-flight token estimate: total text length // 4 (01 §4.1.3)."""
    parts: list[str] = []
    if isinstance(system, str):
        parts.append(system)
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    return len("".join(parts)) // CHARS_PER_TOKEN


class SpyktAnthropicClient:
    """The only sanctioned path to the Anthropic API (01-TECH_SPEC §4.1)."""

    def __init__(
        self,
        config: ModelConfig,
        audit_writer: AuditWriter,
        spend_store: SpendStore,
        escalation_hook: EscalationHook | None = None,
        sdk_client: Any = None,
    ) -> None:
        self.config = config
        self.audit_writer = audit_writer
        self.spend_store = spend_store
        self.escalation_hook = escalation_hook
        self._sdk_client = sdk_client

    @property
    def _sdk(self) -> Any:
        """Lazily construct the real SDK client so no API key is needed at import/init."""
        if self._sdk_client is None:
            import anthropic

            self._sdk_client = anthropic.Anthropic()
        return self._sdk_client

    def call(
        self,
        job: str,
        messages: list[dict[str, Any]],
        *,
        student_id: str,
        prompt_version: str,
        autonomy_level: str,
        max_tokens: int,
        attestation: dict[str, Any] | None = None,
        system: str | None = None,
        agent_name: str | None = None,
    ) -> CallResult:
        agent = agent_name or job
        flags: list[str] = []

        # 1. Resolve model alias from routing (§4.3); 2. budget guard (§4.1.3).
        alias = self.config.routing[job]
        est_tokens_in = estimate_input_tokens(messages, system)
        alias = self._budget_guard(alias, est_tokens_in, max_tokens, student_id, flags)
        model = self.config.models[alias]

        # 3-6. Retention gate, thinking config, request, audit — per request issued.
        response = self._issue_request(
            model,
            messages,
            system=system,
            max_tokens=max_tokens,
            attestation=attestation,
            agent=agent,
            prompt_version=prompt_version,
            autonomy_level=autonomy_level,
            student_id=student_id,
        )

        # 5. Refusal handling (§4.1.1 / PRD §6.2.5): audit + class-5 escalation + one
        # fallback retry that itself passes the budget guard and retention gate.
        if getattr(response, "stop_reason", None) == "refusal":
            self._record_refusal(
                model,
                job,
                agent,
                prompt_version,
                autonomy_level,
                student_id,
                classifier_id=extract_refusal_classifier(response),
            )
            fallback_alias = self.config.fallbacks.get(alias)
            if fallback_alias is not None:
                fallback_alias = self._budget_guard(
                    fallback_alias, est_tokens_in, max_tokens, student_id, flags
                )
                model = self.config.models[fallback_alias]
                response = self._issue_request(
                    model,
                    messages,
                    system=system,
                    max_tokens=max_tokens,
                    attestation=attestation,
                    agent=agent,
                    prompt_version=prompt_version,
                    autonomy_level=autonomy_level,
                    student_id=student_id,
                )
                flags.append("model_fallback")
                if getattr(response, "stop_reason", None) == "refusal":
                    # Refusal beyond fallback (PRD §6.2.5): surfaced again, no retry loop.
                    self._record_refusal(
                        model,
                        job,
                        agent,
                        prompt_version,
                        autonomy_level,
                        student_id,
                        classifier_id=extract_refusal_classifier(response),
                    )

        usage = getattr(response, "usage", None)
        return CallResult(
            response=response,
            model_used=model,
            flags=flags,
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
        )

    def _budget_guard(
        self,
        alias: str,
        est_tokens_in: int,
        max_tokens: int,
        student_id: str,
        flags: list[str],
    ) -> str:
        """01 §4.1.3 / PRD §10: degrade along the fallback chain when over the ceiling.

        Ceilings and pricing come from models.yaml — config, not code. An alias with no
        configured ceiling passes the guard; an over-budget alias with no fallback raises.
        """
        seen: set[str] = set()
        while True:
            if alias in seen:
                raise BudgetExceededError(f"fallback cycle in budget guard at alias '{alias}'")
            seen.add(alias)
            ceiling = self.config.budgets.get(alias)
            if ceiling is None:
                return alias
            pricing = self.config.pricing.get(alias)
            if pricing is None:
                raise BudgetExceededError(
                    f"alias '{alias}' has a budget ceiling but no pricing in models.yaml; "
                    "refusing to skip the budget guard (01 §4.1.3)"
                )
            estimate = (
                est_tokens_in / 1e6 * pricing["input"] + max_tokens / 1e6 * pricing["output"]
            )
            spent = self.spend_store.month_spend(student_id, alias)
            if spent + estimate <= ceiling:
                return alias
            fallback_alias = self.config.fallbacks.get(alias)
            if fallback_alias is None:
                raise BudgetExceededError(
                    f"student {student_id} over ${ceiling:.2f} ceiling for '{alias}' "
                    f"(spent ${spent:.2f} + est ${estimate:.2f}) and no fallback configured"
                )
            logger.info(
                "budget guard: degrading %s -> %s for student %s "
                "(spent=%.4f estimate=%.4f ceiling=%.2f)",
                alias,
                fallback_alias,
                student_id,
                spent,
                estimate,
                ceiling,
            )
            flags.append("degraded_budget")
            alias = fallback_alias

    def _issue_request(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        system: str | None,
        max_tokens: int,
        attestation: dict[str, Any] | None,
        agent: str,
        prompt_version: str,
        autonomy_level: str,
        student_id: str,
    ) -> Any:
        # 3. Retention gate (§4.1.4) on the FINAL model — raises before any request.
        require_pseudonymized(model, attestation)

        kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system is not None:
            kwargs["system"] = system
        # 4. Thinking config (§4.1.2): summarized display for Fable; thinking blocks are
        # passed through opaquely in the response and never parsed here.
        if model.startswith(FABLE_PREFIX):
            kwargs["thinking"] = dict(FABLE_THINKING_CONFIG)

        response = self._sdk.messages.create(**kwargs)

        # 6. Audit row on every model call (CLAUDE.md Phase 1 gate).
        self.audit_writer.write(
            agent=agent,
            model=model,
            prompt_version=prompt_version,
            action="model_call",
            autonomy_level=autonomy_level,
            student_id=student_id,
        )
        return response

    def _record_refusal(
        self,
        model: str,
        job: str,
        agent: str,
        prompt_version: str,
        autonomy_level: str,
        student_id: str,
        *,
        classifier_id: str | None,
    ) -> None:
        """§4.1.1: log the refusal event (with classifier id) + Sentinel class-5 escalation.

        The audit action is ``fable_refusal`` only when the refusing model is Fable
        (§4.1 scopes that event name to Fable; PRD §6.2.5); other models' refusals are
        audited as ``model_refusal``. Both are escalated class-5 — over-escalating is
        the safe direction. audit_log has no detail column (packages/audit schema), so
        the classifier id rides as a colon suffix on the action string.
        """
        base_action = "fable_refusal" if model.startswith(FABLE_PREFIX) else "model_refusal"
        action = base_action if classifier_id is None else f"{base_action}:{classifier_id}"
        logger.warning(
            "%s: model=%s job=%s student=%s classifier=%s",
            base_action,
            model,
            job,
            student_id,
            classifier_id or "<none>",
        )
        self.audit_writer.write(
            agent=agent,
            model=model,
            prompt_version=prompt_version,
            action=action,
            autonomy_level=autonomy_level,
            student_id=student_id,
        )
        reason = (
            f"model refusal (stop_reason=refusal, classifier={classifier_id or '<none>'}) "
            f"on {model} for job '{job}'"
        )
        if self.escalation_hook is not None:
            self.escalation_hook(escalation_class=5, reason=reason)
        else:
            # PRD §6.2 makes escalation to a human unconditional. The escalation queue is
            # a Phase-2 unit; until it is wired this seam must fail LOUDLY, not silently.
            logger.error(
                "class-5 escalation had NO delivery hook wired (PRD §6.2 requires "
                "unconditional human escalation); refusal audited but undelivered: %s",
                reason,
            )
