"""A8 Sentinel tests (PRD §6.2; 01-TECH_SPEC §5).

All model traffic is stubbed (CLAUDE.md: no real Anthropic API calls in tests).
Stub pattern follows packages/anthropic-client/tests/test_client.py.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from spykt_anthropic_client import SpyktAnthropicClient, load_model_config
from spykt_contracts import SpecialistInput, SpecialistOutput, validate
from spykt_workers.prompts import sentinel_v1
from spykt_workers.sentinel import DEFAULT_CONFIDENCE_THRESHOLD, run_sentinel

REPO_ROOT = Path(__file__).resolve().parents[3]
HELDOUT_PATH = REPO_ROOT / "evals" / "sentinel" / "corpus_heldout.jsonl"
PROMPT_MODULE_PATH = REPO_ROOT / "services" / "workers" / "spykt_workers" / "prompts" / "sentinel_v1.py"


def sentinel_input(text="finished the outline, moving to draft tomorrow", job_id="job-1"):
    return SpecialistInput.model_validate(
        {
            "job_id": job_id,
            "student_pseudonym": "stu-pseudo-1",
            "task": text,
            "context_refs": [],
            "budget_tokens": 2000,
            "autonomy_ceiling": "L0",
        }
    )


def stub_response(text, tokens_in=150, tokens_out=60, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
    )


class StubSDK:
    """Recorder standing in for anthropic.Anthropic."""

    def __init__(self, responses):
        self.requests: list[dict] = []
        self._responses = list(responses)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


class RaisingSDK:
    def __init__(self, exc):
        self._exc = exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        raise self._exc


class RecordingAudit:
    def __init__(self):
        self.rows: list[dict] = []

    def write(self, **kwargs):
        self.rows.append(kwargs)


class ZeroSpend:
    def month_spend(self, student_id, model_alias):
        return 0.0


def make_client(sdk, audit=None):
    return SpyktAnthropicClient(
        config=load_model_config(),
        audit_writer=audit if audit is not None else RecordingAudit(),
        spend_store=ZeroSpend(),
        sdk_client=sdk,
    )


def model_json(cls=4, severity="none", evidence="none", action="none", confidence=0.9):
    return json.dumps(
        {
            "class": cls,
            "severity": severity,
            "evidence_ref": evidence,
            "recommended_action": action,
            "confidence": confidence,
        }
    )


def run(reply_text, text="hello", **kwargs):
    client = make_client(StubSDK([stub_response(reply_text)]))
    return run_sentinel(sentinel_input(text), client, **kwargs)


# --- Happy path -----------------------------------------------------------------


def test_happy_path_produces_schema_valid_output():
    audit = RecordingAudit()
    sdk = StubSDK(
        [stub_response(model_json(cls=3, severity="elevated", evidence="copied essay", action="coach_review"))]
    )
    client = make_client(sdk, audit=audit)
    out = run_sentinel(sentinel_input("integrity-adjacent message"), client)

    assert isinstance(out, SpecialistOutput)
    assert out.status == "ok"
    assert out.confidence == pytest.approx(0.9)
    # PRD §6.2: classes 1-3 route Sentinel → human unconditionally.
    assert out.escalate is not None
    assert out.escalate.escalation_class == 3
    # Envelope and result both validate against the wire schemas.
    wire = out.to_contract_dict()
    validate("specialist_output", wire)
    validate("a8_sentinel", wire["result"])
    assert wire["result"]["class"] == 3
    # Audit stamp: routed model (haiku per models.yaml), versioned prompt, real token counts.
    config = load_model_config()
    assert out.audit.model == config.model_for("sentinel")
    assert out.audit.prompt_version == sentinel_v1.PROMPT_VERSION == "sentinel-v1"
    assert (out.audit.tokens_in, out.audit.tokens_out) == (150, 60)
    # The client wrote a model_call audit row for the request (CLAUDE.md Phase 1 gate).
    assert [r["action"] for r in audit.rows] == ["model_call"]
    # System prompt and message passed through as-is (pre-gateway by design).
    request = sdk.requests[0]
    assert request["system"] == sentinel_v1.SYSTEM_PROMPT
    assert request["messages"] == [{"role": "user", "content": "integrity-adjacent message"}]


def test_class_1_sets_wellbeing_escalation():
    out = run(
        model_json(
            cls=1,
            severity="acute",
            evidence="not safe",
            action="immediate_coach_alert_push_sms",
            confidence=0.97,
        )
    )
    assert out.status == "ok"
    assert out.escalate is not None
    assert out.escalate.escalation_class == 1
    assert "severity=acute" in out.escalate.reason
    validate("specialist_output", out.to_contract_dict())


def test_class_2_sets_family_conflict_escalation():
    out = run(model_json(cls=2, severity="elevated", evidence="dad", action="coach_review_24h"))
    assert out.escalate is not None
    assert out.escalate.escalation_class == 2


def test_class_3_sets_integrity_escalation():
    # PRD §6.2 class 3 (integrity) routes to a coach unconditionally; the
    # escalation queue (escalation.py SLA_BY_CLASS) carries its 48h SLA. A
    # confident class-3 must therefore emit the escalate directive — status
    # 'ok' with escalate=None would be a fail-open path on the safety spine.
    out = run(model_json(cls=3, severity="elevated", evidence="copied essay", action="coach_review"))
    assert out.status == "ok"
    assert out.escalate is not None
    assert out.escalate.escalation_class == 3
    assert "severity=elevated" in out.escalate.reason
    validate("specialist_output", out.to_contract_dict())


def test_low_confidence_class_3_still_escalates():
    # Over-escalate by design (01 §9): low confidence reroutes the envelope,
    # it never suppresses an integrity escalation.
    out = run(model_json(cls=3, severity="elevated", evidence="x", action="coach_review", confidence=0.3))
    assert out.status == "low_confidence"
    assert out.escalate is not None
    assert out.escalate.escalation_class == 3


def test_confident_class_4_and_5_do_not_escalate_here():
    # Class 4 routes via status/error envelope; class 5 is raised by the client
    # refusal middleware (01 §4.1.1), not this classifier.
    out4 = run(model_json(cls=4, severity="none", confidence=0.9))
    assert out4.status == "ok" and out4.escalate is None
    out5 = run(
        model_json(cls=5, severity="elevated", evidence="x", action="engineering_and_coach_review")
    )
    assert out5.status == "ok" and out5.escalate is None


def test_json_wrapped_in_code_fence_still_parses():
    fenced = "```json\n" + model_json(cls=1, severity="acute", evidence="x", action="y", confidence=0.95) + "\n```"
    out = run(fenced)
    assert out.status == "ok"
    assert out.escalate is not None and out.escalate.escalation_class == 1


# --- Failure posture: error + class-4, never crash, never drop -------------------


@pytest.mark.parametrize(
    "bad_reply",
    [
        "I think this student is fine.",  # prose, no JSON
        "{not json at all",
        "[1, 2, 3]",  # JSON but not an object
        json.dumps({"class": 9, "severity": "acute", "evidence_ref": "x", "recommended_action": "y", "confidence": 0.9}),  # noqa: E501 — schema-invalid class
        json.dumps({"severity": "acute", "evidence_ref": "x", "recommended_action": "y", "confidence": 0.9}),  # missing class
        json.dumps({"class": 1, "severity": "acute", "evidence_ref": "x", "recommended_action": "y"}),  # missing confidence
        json.dumps({"class": 1, "severity": "acute", "evidence_ref": "x", "recommended_action": "y", "confidence": 1.4}),  # noqa: E501 — confidence out of range
        json.dumps({"class": 1, "severity": "acute", "evidence_ref": "x", "recommended_action": "y", "confidence": True}),  # noqa: E501 — bool is not a confidence
    ],
)
def test_malformed_output_fails_toward_humans(bad_reply):
    out = run(bad_reply)
    assert out.status == "error"
    assert out.confidence == 0.0
    assert out.escalate is not None
    assert out.escalate.escalation_class == 4
    validate("specialist_output", out.to_contract_dict())


def test_model_call_exception_never_crashes_and_escalates_class_4():
    client = make_client(RaisingSDK(RuntimeError("api down")))
    out = run_sentinel(sentinel_input("any message"), client)
    assert out.status == "error"
    assert out.escalate is not None and out.escalate.escalation_class == 4
    assert "api down" in out.result["error"]
    validate("specialist_output", out.to_contract_dict())


# --- Confidence threshold ---------------------------------------------------------


def test_below_threshold_confidence_yields_low_confidence_status():
    out = run(model_json(cls=4, severity="none", confidence=0.5))
    assert out.status == "low_confidence"
    assert out.confidence == pytest.approx(0.5)
    assert out.escalate is None


def test_low_confidence_class_1_still_escalates():
    # Over-escalate by design (01 §9): low confidence reroutes to the coach
    # queue, it never suppresses a wellbeing escalation.
    out = run(model_json(cls=1, severity="acute", evidence="x", action="y", confidence=0.3))
    assert out.status == "low_confidence"
    assert out.escalate is not None
    assert out.escalate.escalation_class == 1


def test_threshold_is_configurable():
    reply = model_json(cls=4, severity="none", confidence=0.75)
    assert run(reply).status == "ok"  # default threshold 0.7
    assert DEFAULT_CONFIDENCE_THRESHOLD == 0.7
    assert run(reply, confidence_threshold=0.8).status == "low_confidence"


# --- Heldout-corpus leak guard (evals/sentinel/README.md usage rules) -------------


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def test_prompt_contains_no_heldout_text():
    """corpus_heldout.jsonl is for recall measurement ONLY: no case may appear in the prompt module."""
    prompt_source = _normalize(PROMPT_MODULE_PATH.read_text())
    prompt_string = _normalize(sentinel_v1.SYSTEM_PROMPT)
    cases = [json.loads(line) for line in HELDOUT_PATH.read_text().splitlines() if line.strip()]
    assert len(cases) > 0
    for case in cases:
        text = _normalize(case["text"])
        assert text not in prompt_source, f"heldout case {case['id']} leaked into the prompt module"
        assert text not in prompt_string, f"heldout case {case['id']} leaked into SYSTEM_PROMPT"


def test_prompt_examples_come_from_train_corpus():
    train_texts = {
        json.loads(line)["id"]: json.loads(line)["text"]
        for line in (REPO_ROOT / "evals" / "sentinel" / "corpus_train.jsonl").read_text().splitlines()
        if line.strip()
    }
    prompt = _normalize(sentinel_v1.SYSTEM_PROMPT)
    for example_id in sentinel_v1.TRAIN_EXAMPLE_IDS:
        assert example_id in train_texts
        assert _normalize(train_texts[example_id]) in prompt
