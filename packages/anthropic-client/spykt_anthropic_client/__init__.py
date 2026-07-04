"""Shared Anthropic client path (01-TECH_SPEC §4.1).

`SpyktAnthropicClient` is the only sanctioned path to the Anthropic API, carrying
the four mandatory middlewares: refusal fallback, thinking config, budget guard,
and retention gate. Contract tests live at the G1 gate.
"""

from spykt_anthropic_client.client import (
    AuditWriter,
    BudgetExceededError,
    CallResult,
    EscalationHook,
    SpendStore,
    SpyktAnthropicClient,
)
from spykt_anthropic_client.config import ModelConfig, load_model_config
from spykt_anthropic_client.retention import RetentionGateError, require_pseudonymized

__all__ = [
    "AuditWriter",
    "BudgetExceededError",
    "CallResult",
    "EscalationHook",
    "ModelConfig",
    "RetentionGateError",
    "SpendStore",
    "SpyktAnthropicClient",
    "load_model_config",
    "require_pseudonymized",
]
