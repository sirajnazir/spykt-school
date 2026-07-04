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
    return ModelConfig(
        models=raw["models"],
        routing=raw["routing"],
        fallbacks=raw["fallbacks"],
        embedding_dimensions=raw["embeddings"]["dimensions"],
    )
