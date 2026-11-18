import hashlib
import re
from pathlib import Path

import pytest

from brain_gate.api import Integration
from brain_gate.contextual import GlinerDetector
from brain_gate.engine import Engine
from brain_gate.policy import PolicyStore
from brain_gate.rules import RuleDetector

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "test-token-no-real"


class FakeGliner:
    """Sustituto de GLiNER con su misma firma: detecta nombres y direcciones de una lista."""

    def __init__(self, entities: dict[str, str], fail: bool = False) -> None:
        self.entities = entities  # texto -> etiqueta del modelo
        self.fail = fail
        self.calls: list[str] = []

    def predict_entities(self, text, labels, threshold=0.3):
        if self.fail:
            raise RuntimeError("modelo caído")
        self.calls.append(text)
        out = []
        for value, label in self.entities.items():
            for m in re.finditer(re.escape(value), text):
                out.append({"start": m.start(), "end": m.end(), "label": label, "score": 0.9, "text": value})
        return out


NAMES = {
    "María López": "person",
    "José Ángel Núñez": "person",
    "Calle Mayor 12, 3º B, Madrid": "address",
}
LABELS = {"person": "PERSONA", "address": "DIRECCION"}


@pytest.fixture
def policies():
    return PolicyStore.default()


@pytest.fixture
def policy(policies):
    return policies.get("brain-external-es")


@pytest.fixture
def fake_model():
    return FakeGliner(NAMES)


@pytest.fixture
def engine(fake_model):
    return Engine([RuleDetector(), GlinerDetector(fake_model, LABELS)])


@pytest.fixture
def integrations():
    return {"legal-brain-gateway": Integration("legal-brain-gateway", hashlib.sha256(TOKEN.encode()).hexdigest(), {"brain-external-es"})}
