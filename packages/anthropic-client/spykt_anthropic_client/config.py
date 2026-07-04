"""Loader for models.yaml — the single source of model strings (01-TECH_SPEC §4.3 / GAP-11)."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ModelConfig:
    models: dict[str, str]
    routing: dict[str, str]
    fallbacks: dict[str, str]
    embedding_dimensions: int
    # Pricing ($ per MTok in/out) and per-student monthly ceilings (USD), keyed by
    # model alias. Budgets are config, not code (PRD §10 / 01-TECH_SPEC §4.1.3).
    pricing: dict[str, dict[str, float]]
    budgets: dict[str, float]

    def model_for(self, job: str) -> str:
        """Resolve a routing job name (e.g. 'genome_scorer') to a model string."""
        return self.models[self.routing[job]]

    def fallback_for(self, alias: str) -> str | None:
        fb = self.fallbacks.get(alias)
        return self.models[fb] if fb else None


def find_models_yaml(start: Path | None = None) -> Path:
    """Walk up from `start` (or this file) to the repo-root models.yaml."""
    here = start or Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "models.yaml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("models.yaml not found walking up from " + str(here))


def load_model_config(path: Path | None = None) -> ModelConfig:
    raw = yaml.safe_load((path or find_models_yaml()).read_text())
    # pricing/budgets are strict-keyed like models/routing/fallbacks: a models.yaml
    # missing either section must fail HERE, loudly, rather than silently turning the
    # mandatory budget guard (01 §4.1.3 / PRD §10) into a no-op via empty defaults.
    return ModelConfig(
        models=raw["models"],
        routing=raw["routing"],
        fallbacks=raw["fallbacks"],
        embedding_dimensions=raw["embeddings"]["dimensions"],
        pricing=raw["pricing"],
        budgets=raw["budgets"],
    )
