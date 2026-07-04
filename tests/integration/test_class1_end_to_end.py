"""Cross-unit class-1 flow: distress message → Sentinel → escalation row + coach push/SMS.

Local analogue of the Phase 2 gate criterion "class-1 end-to-end alert ≤5s in staging"
(CLAUDE.md §3; flow F3 in 02-UIUX §7): with the model stubbed, the entire pipeline —
classification parsing, contract validation, escalation row, synchronous push+SMS
fan-out to assigned coach + on-call — must complete in well under the 5s budget. The
staging re-run with live Haiku is a G2 item (D-005).
"""

import json
import time

from spykt_anthropic_client import SpyktAnthropicClient, load_model_config
from spykt_audit import InMemoryAuditWriter
from spykt_contracts import SpecialistInput
from spykt_notify import RecorderPush, RecorderSms
from spykt_workers.escalation import CoachContact, EscalationService, InMemoryEscalationStore
from spykt_workers.sentinel import run_sentinel

CLASS1_JSON = json.dumps(
    {
        "class": 1,
        "severity": "acute",
        "evidence_ref": "message",
        "recommended_action": "immediate_human_contact",
        "confidence": 0.96,
    }
)


class StubResponse:
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.usage = type("U", (), {"input_tokens": 50, "output_tokens": 40})()


class StubSDK:
    def __init__(self):
        self.messages = self

    def create(self, **kwargs):
        return StubResponse(CLASS1_JSON)


class ZeroSpend:
    def month_spend(self, student_id, model_alias):
        return 0.0


def test_class1_distress_reaches_coaches_within_budget():
    audit = InMemoryAuditWriter()
    client = SpyktAnthropicClient(load_model_config(), audit, ZeroSpend(), sdk_client=StubSDK())
    push, sms = RecorderPush(), RecorderSms()
    service = EscalationService(InMemoryEscalationStore(), push, sms, audit)

    coach = CoachContact(coach_id="coach-1", phone="+15550000001")
    oncall = [CoachContact(coach_id="coach-2", phone="+15550000002")]

    started = time.monotonic()
    output = run_sentinel(
        SpecialistInput(
            job_id="job-1",
            student_pseudonym="Student-abc123",
            task="i don't want to be here anymore. nothing matters",
            context_refs=[],
            budget_tokens=1000,
            autonomy_ceiling="L0",
        ),
        client,
    )
    assert output.escalate is not None and output.escalate.escalation_class == 1

    row = service.handle(
        output.result_as_sentinel() if hasattr(output, "result_as_sentinel") else _sentinel_view(output),
        student_id="stu-1",
        assigned_coach=coach,
        oncall=oncall,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"class-1 pipeline took {elapsed:.2f}s (budget 5s)"
    # Synchronous delivery to assigned coach + on-call, both channels, before handle() returned.
    assert {to for to, _ in push.sent} == {"coach-1", "coach-2"}
    assert {to for to, _ in sms.sent} == {"+15550000001", "+15550000002"}
    assert row.sla_due is not None
    # The whole path is audited: sentinel model call + escalation actions.
    actions = [r["action"] for r in audit.rows]
    assert "model_call" in actions


def _sentinel_view(output):
    """Adapt SpecialistOutput.result (a8 dict) to the attribute view handle() accepts."""
    r = output.result

    class View:
        escalation_class = r["class"]
        severity = r.get("severity")
        evidence_ref = r.get("evidence_ref")
        recommended_action = r.get("recommended_action")

    return View()
