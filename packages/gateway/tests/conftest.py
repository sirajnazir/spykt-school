import pytest

from spykt_gateway import InMemoryPseudonymStore, PseudonymizationGateway

# Known-entity profile used across the adversarial set.
MAYA = {
    "name": "Maya Chen",
    "nicknames": ["May-May"],
    "family_names": ["Wei Chen", "Lily Chen"],
    "school": "Lincoln High",
    "city": "Portland",
    "emails": ["maya.chen@gmail.com"],
    "phones": ["503-555-0142"],
    "handles": ["@mayabuilds"],
}


@pytest.fixture
def store() -> InMemoryPseudonymStore:
    return InMemoryPseudonymStore()


@pytest.fixture
def gateway(store: InMemoryPseudonymStore) -> PseudonymizationGateway:
    # nlp=None → shared module-level en_core_web_sm cache (loaded once per session).
    return PseudonymizationGateway(store)
