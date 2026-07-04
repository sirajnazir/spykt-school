"""A8 Escalation Sentinel (PRD §6.2; 01-TECH_SPEC §5).

`run_sentinel` classifies one live student message into an escalation class:
1 wellbeing, 2 family conflict/pressure, 3 integrity, 4 low-confidence/other
(also the no-escalation class, severity "none"), 5 model refusal anomaly.

Routing (01 §4): job "sentinel" → Haiku 4.5 via models.yaml. Sentinel is
Sonnet/Haiku-plane and sees live pre-gateway content BY DESIGN — no
pseudonymization attestation is needed for non-Fable models, and the message
text is passed through as-is. The client's retention gate still stands: if
routing ever pointed this job at Fable without an attestation, the call is
blocked there and this module fails toward humans (error + class-4), never
around the gate.

Failure posture (prime directive 1 — fail toward humans, never crash, never
silently drop):
- Malformed/unparseable/schema-invalid model output → status "error" plus an
  escalate {class: 4} directive so a human still looks at the message.
- A failed model call gets the same error + class-4 envelope.
- A class-1/2/3 classification sets `escalate` even when confidence is below
  threshold — over-escalate by design (01 §9); low confidence changes routing
  (status "low_confidence" → coach queue, PRD §6.2.4), not whether a human
  sees it. Class 3 (integrity) escalates unconditionally like 1 and 2: PRD
  §6.2 routes it to a coach, and the escalation queue owns its SLA.

Class-1 queue-bypass + push/SMS delivery is the escalation queue's job
(01 §5: class-1 bypasses queue ordering), keyed off the `escalate` directive
emitted here.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import jsonschema

from spykt_anthropic_client import SpyktAnthropicClient
from spykt_contracts import SpecialistInput, SpecialistOutput, validate
from spykt_workers.prompts.sentinel_v1 import PROMPT_VERSION, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

SENTINEL_JOB = "sentinel"
SENTINEL_AGENT = "sentinel"
MAX_TOKENS = 512
# 01 §5: low_confidence is "below per-agent threshold, config". This default is
# the call-site seam; the orchestrator/dispatcher unit is responsible for wiring
# `confidence_threshold` from real per-agent config when it invokes run_sentinel.
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

# Classes 1-3 produce an escalate directive here (PRD §6.2: wellbeing, family
# conflict, and integrity all route Sentinel → human unconditionally; the
# escalation queue carries SLAs for all three in escalation.py SLA_BY_CLASS).
# Class 4 routes via status ("low_confidence" → coach queue) and the error
# envelope; class 5 is raised by the client refusal middleware at the point of
# refusal (01 §4.1.1), not by this classifier.
ESCALATE_CLASSES = frozenset({1, 2, 3})


def _response_text(response: Any) -> str:
    """Concatenate text blocks from an SDK response (objects or dicts); thinking blocks ignored."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if isinstance(block, dict):
            if block.get("type") in (None, "text") and isinstance(block.get("text"), str):
                parts.append(block["text"])
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str) and getattr(block, "type", "text") == "text":
                parts.append(text)
    return "".join(parts)


def _parse_json_object(text: str) -> dict[str, Any]:
    """Parse the model's strict-JSON reply; tolerate wrapping noise (code fences, prose).

    Raises ValueError when no JSON object can be recovered — the caller turns
    that into error + class-4, never a crash.
    """
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            doc = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            return doc
        break
    raise ValueError(f"sentinel reply is not a JSON object: {text[:200]!r}")


def _pop_confidence(doc: dict[str, Any]) -> float:
    """Extract and remove the confidence field (the a8 schema does not carry it)."""
    confidence = doc.pop("confidence", None)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError(f"sentinel reply missing numeric confidence, got {confidence!r}")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError(f"sentinel confidence {confidence!r} outside [0.0, 1.0]")
    return float(confidence)


def _error_output(
    job_id: str,
    *,
    model: str,
    reason: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> SpecialistOutput:
    """Fail toward humans: status 'error' + class-4 escalation, never a silent drop."""
    logger.error("sentinel error on job %s: %s", job_id, reason)
    return SpecialistOutput.model_validate(
        {
            "job_id": job_id,
            "status": "error",
            "confidence": 0.0,
            "result": {"error": reason},
            "escalate": {"class": 4, "reason": f"sentinel failed; human review required: {reason}"},
            "audit": {
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            },
        }
    )


def run_sentinel(
    specialist_input: SpecialistInput,
    client: SpyktAnthropicClient,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> SpecialistOutput:
    """Classify `specialist_input.task` (the live student message) per PRD §6.2.

    Returns a schema-valid SpecialistOutput whose `result` matches the a8
    contract {class, severity, evidence_ref, recommended_action}; the model's
    confidence rides in the envelope. Never raises on bad model output.
    """
    try:
        call = client.call(
            SENTINEL_JOB,
            [{"role": "user", "content": specialist_input.task}],
            student_id=specialist_input.student_pseudonym,
            prompt_version=PROMPT_VERSION,
            autonomy_level=specialist_input.autonomy_ceiling,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            agent_name=SENTINEL_AGENT,
        )
    except Exception as exc:  # noqa: BLE001 — a crashed Sentinel drops the message; escalate instead.
        logger.exception("sentinel model call failed for job %s", specialist_input.job_id)
        try:
            model = client.config.model_for(SENTINEL_JOB)
        except Exception:  # noqa: BLE001 — even a broken config must not mask the escalation.
            model = "unknown"
        return _error_output(
            specialist_input.job_id,
            model=model,
            reason=f"model call failed: {type(exc).__name__}: {exc}",
        )

    raw_text = _response_text(call.response)
    try:
        doc = _parse_json_object(raw_text)
        confidence = _pop_confidence(doc)
        validate("a8_sentinel", doc)
    except (ValueError, jsonschema.ValidationError) as exc:
        return _error_output(
            specialist_input.job_id,
            model=call.model_used,
            reason=f"malformed sentinel output: {exc}",
            tokens_in=call.tokens_in,
            tokens_out=call.tokens_out,
        )

    escalate: dict[str, Any] | None = None
    if doc["class"] in ESCALATE_CLASSES:
        escalate = {
            "class": doc["class"],
            "reason": (
                f"sentinel class-{doc['class']} (severity={doc['severity']}, "
                f"confidence={confidence:.2f}): evidence={doc['evidence_ref']}; "
                f"recommended_action={doc['recommended_action']}"
            ),
        }

    status = "ok" if confidence >= confidence_threshold else "low_confidence"
    return SpecialistOutput.model_validate(
        {
            "job_id": specialist_input.job_id,
            "status": status,
            "confidence": confidence,
            "result": doc,
            "escalate": escalate,
            "audit": {
                "model": call.model_used,
                "prompt_version": PROMPT_VERSION,
                "tokens_in": call.tokens_in,
                "tokens_out": call.tokens_out,
            },
        }
    )
