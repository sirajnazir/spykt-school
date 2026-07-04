from spykt_anthropic_client import load_model_config


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
