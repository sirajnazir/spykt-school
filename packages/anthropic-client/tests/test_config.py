import pytest
import yaml

from spykt_anthropic_client import load_model_config
from spykt_anthropic_client.config import find_models_yaml


def test_pinned_model_strings():
    cfg = load_model_config()
    assert cfg.models["fable"] == "claude-fable-5"
    assert cfg.models["opus"] == "claude-opus-4-8"
    assert cfg.models["sonnet"] == "claude-sonnet-4-6"
    assert cfg.models["haiku"] == "claude-haiku-4-5-20251001"
    assert cfg.models["embeddings"] == "voyage-3-large"
    assert cfg.embedding_dimensions == 1536


def test_fable_jobs_route_to_fable():
    cfg = load_model_config()
    for job in ("genome_scorer", "pathway_planner_weekly", "narrative_architect", "verifier"):
        assert cfg.model_for(job) == "claude-fable-5"


def test_live_turns_never_touch_fable():
    """PRD §4: live turns never touch Fable (latency + retention constraint)."""
    cfg = load_model_config()
    for job in ("zuzu_live", "zuzu_nudge", "orchestrator", "sentinel", "evidence_curator"):
        assert cfg.model_for(job) != "claude-fable-5"


def test_fable_falls_back_to_opus():
    cfg = load_model_config()
    assert cfg.fallback_for("fable") == "claude-opus-4-8"


@pytest.mark.parametrize("missing_section", ["pricing", "budgets"])
def test_missing_pricing_or_budgets_section_fails_loudly(tmp_path, missing_section):
    """A models.yaml without pricing/budgets must fail at load, not silently disable the
    mandatory budget guard (01 §4.1.3 / PRD §10) via empty defaults."""
    raw = yaml.safe_load(find_models_yaml().read_text())
    del raw[missing_section]
    broken = tmp_path / "models.yaml"
    broken.write_text(yaml.safe_dump(raw))

    with pytest.raises(KeyError):
        load_model_config(broken)


def test_every_budget_ceiling_has_pricing():
    """Each configured ceiling needs pricing for the pre-flight estimate (01 §4.1.3)."""
    cfg = load_model_config()
    for alias in cfg.budgets:
        assert alias in cfg.pricing, f"budget ceiling for '{alias}' has no pricing"
