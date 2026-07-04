import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("run_gate", Path(__file__).resolve().parents[1] / "run_gate.py")
run_gate = importlib.util.module_from_spec(spec)
sys.modules["run_gate"] = run_gate
spec.loader.exec_module(run_gate)


def test_gate_passes_vacuously_with_no_pinned_suites():
    assert run_gate.main() == 0


def test_gate_fails_loudly_if_suite_pinned_without_runner(tmp_path, monkeypatch):
    (tmp_path / "genome").mkdir(parents=True)
    (tmp_path / "genome" / "v1.yaml").write_text("suite: genome-consistency\n")
    monkeypatch.setattr(run_gate, "PINNED_DIR", tmp_path)
    assert run_gate.main() == 1
