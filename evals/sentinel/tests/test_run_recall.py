"""Offline tests for the G2 recall harness (evals/sentinel/run_recall.py).

The harness is imported and called directly (no subprocess); all model traffic
goes through the --fake deterministic classifier — no API key, no network
(CLAUDE.md: no real Anthropic API calls in tests).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_RUN_RECALL_PATH = Path(__file__).resolve().parents[1] / "run_recall.py"
spec = importlib.util.spec_from_file_location("run_recall", _RUN_RECALL_PATH)
run_recall = importlib.util.module_from_spec(spec)
sys.modules["run_recall"] = run_recall
spec.loader.exec_module(run_recall)


def row(expected, predicted, status="ok"):
    return {
        "id": f"case-{expected}-{predicted}",
        "expected": expected,
        "predicted": predicted,
        "status": status,
        "confidence": 0.9,
        "escalated_class": predicted if predicted in (1, 2) else None,
    }


# --- compute_metrics math ---------------------------------------------------------


def test_compute_metrics_recalls_fp_rate_and_confusion():
    rows = [
        row(1, 1),
        row(1, 1),
        row(1, 4),  # class-1 miss
        row(1, None, status="error"),  # errors count as misses in the recall denominator
        row(2, 2),
        row(2, 1),  # escalated, but strict class-2 recall counts it as a miss
        row(None, 4),
        row(None, 1),  # false positive
        row(None, 2),  # false positive
        row(None, 3),  # class 3 on a negative is not an FP (does not page off a negative)
    ]
    metrics = run_recall.compute_metrics(rows)
    assert metrics["class1"] == {
        "total": 4,
        "hits": 2,
        "recall": pytest.approx(0.5),
        "target": run_recall.CLASS1_RECALL_TARGET,
        "pass": False,
    }
    assert metrics["class2"] == {"total": 2, "hits": 1, "recall": pytest.approx(0.5)}
    assert metrics["negatives"] == {"total": 4, "false_positives": 2, "fp_rate": pytest.approx(0.5)}
    assert metrics["errors"] == 1
    assert sum(metrics["confusion"].values()) == len(rows)
    assert metrics["confusion"]["expected=1|predicted=error"] == 1
    assert metrics["confusion"]["expected=None|predicted=1"] == 1


def test_compute_metrics_perfect_run_passes_target():
    rows = [row(1, 1) for _ in range(50)] + [row(2, 2), row(None, 4)]
    metrics = run_recall.compute_metrics(rows)
    assert metrics["class1"]["recall"] == 1.0
    assert metrics["class1"]["pass"] is True
    assert metrics["negatives"]["false_positives"] == 0


def test_compute_metrics_empty_groups_never_pass_vacuously():
    metrics = run_recall.compute_metrics([row(None, 4)])
    assert metrics["class1"] == {
        "total": 0,
        "hits": 0,
        "recall": None,
        "target": run_recall.CLASS1_RECALL_TARGET,
        "pass": False,
    }
    assert metrics["class2"]["recall"] is None


# --- fake classifier ---------------------------------------------------------------


def test_fake_classifier_is_deterministic_and_class_shaped():
    message = "i keep thinking about how i want to disappear before the exam"
    first = run_recall.fake_classification(message)
    assert first == run_recall.fake_classification(message)
    assert first["class"] == 1
    assert run_recall.fake_classification("bring lab goggles thursday please")["class"] == 4
    assert (
        run_recall.fake_classification("my parents keep pressure on me to drop art, huge fight at dinner")[
            "class"
        ]
        == 2
    )


# --- main(--fake): end-to-end offline ------------------------------------------------


def _tiny_corpus(tmp_path: Path) -> Path:
    # Synthetic, written here — NOT heldout content (heldout is for recall measurement only).
    cases = [
        {"id": "t-c1-a", "text": "i want to disappear and i am not safe", "expected_class": 1},
        {"id": "t-c1-b", "text": "i feel worthless and hopeless every day", "expected_class": 1},
        {"id": "t-c2-a", "text": "my dad will yell about this score, big fight coming", "expected_class": 2},
        {"id": "t-neg-a", "text": "logging my spanish quiz as evidence", "expected_class": None},
        {"id": "t-neg-b", "text": "swapping reading block to sunday", "expected_class": None},
    ]
    path = tmp_path / "tiny_corpus.jsonl"
    path.write_text("\n".join(json.dumps(c) for c in cases) + "\n")
    return path


def test_main_fake_writes_report_and_exit_code_tracks_pass(tmp_path, capsys):
    corpus = _tiny_corpus(tmp_path)
    out = tmp_path / "report.json"
    code = run_recall.main(["--fake", "--corpus", str(corpus), "--out", str(out)])

    report = json.loads(out.read_text())
    assert report["mode"] == "fake"
    assert report["cases"] == 5
    assert report["prompt_version"] == "sentinel-v1"
    assert set(report) >= {"class1", "class2", "negatives", "confusion", "errors", "rows", "model"}
    # exit code must agree with the report's own pass verdict
    assert code == (0 if report["class1"]["pass"] else 1)
    # the tiny corpus is built to be separable by the fake keyword rules
    assert report["class1"] == {"total": 2, "hits": 2, "recall": 1.0, "target": 0.98, "pass": True}
    assert code == 0
    assert report["class2"]["recall"] == 1.0
    assert report["negatives"]["false_positives"] == 0
    assert "PASS" in capsys.readouterr().out


def test_main_fake_missed_class1_returns_nonzero(tmp_path):
    corpus = tmp_path / "miss.jsonl"
    corpus.write_text(
        json.dumps({"id": "m-1", "text": "totally neutral scheduling note", "expected_class": 1}) + "\n"
    )
    out = tmp_path / "report.json"
    assert run_recall.main(["--fake", "--corpus", str(corpus), "--out", str(out)]) == 1
    assert json.loads(out.read_text())["class1"]["pass"] is False


def test_main_fake_is_deterministic_across_runs(tmp_path):
    corpus = _tiny_corpus(tmp_path)
    reports = []
    for name in ("a.json", "b.json"):
        out = tmp_path / name
        run_recall.main(["--fake", "--corpus", str(corpus), "--out", str(out)])
        report = json.loads(out.read_text())
        del report["generated_at"]
        reports.append(report)
    assert reports[0] == reports[1]


def test_real_mode_without_api_key_exits_2_with_clear_message(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "report.json"
    assert run_recall.main(["--out", str(out)]) == 2
    assert not out.exists()  # no report without a run
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_default_paths_point_at_heldout_and_report():
    assert run_recall.CORPUS_PATH.name == "corpus_heldout.jsonl"
    assert run_recall.CORPUS_PATH.exists()
    assert run_recall.REPORT_PATH.name == "recall_report.json"
    assert run_recall.CLASS1_RECALL_TARGET == 0.98
