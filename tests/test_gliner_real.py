"""Pruebas con los pesos reales de GLiNER. Se omiten si no están en disco.

Ruta: ``BRAIN_GATE_GLINER_PATH`` o ``models/gliner_multi_pii-v1``.
"""

import os
import socket
from pathlib import Path

import pytest

from conftest import ROOT

MODEL_DIR = Path(os.environ.get("BRAIN_GATE_GLINER_PATH", ROOT / "models" / "gliner_multi_pii-v1"))
pytestmark = [
    pytest.mark.gliner,
    pytest.mark.skipif(not (MODEL_DIR / "pytorch_model.bin").is_file(), reason="pesos GLiNER no presentes"),
]


@pytest.fixture(scope="module")
def real_engine():
    pytest.importorskip("gliner")
    from brain_gate.api import GLINER_LABELS
    from brain_gate.contextual import GlinerDetector
    from brain_gate.engine import Engine
    from brain_gate.rules import RuleDetector

    # La carga no debe necesitar red: cualquier conexión saliente falla la prueba.
    mp = pytest.MonkeyPatch()
    def deny(*a, **k):
        raise AssertionError("intento de conexión de red durante la carga del modelo")
    mp.setattr(socket.socket, "connect", deny)
    mp.setattr(socket, "create_connection", deny)
    try:
        det = GlinerDetector.from_local_path(str(MODEL_DIR), GLINER_LABELS)
    finally:
        mp.undo()
    return Engine([RuleDetector(), det])


def test_contract_example_real(real_engine, policy):
    r = real_engine.mask("María López pidió que le escribieran a maria@example.com", policy)
    assert r.masked_text == "[PERSONA] pidió que le escribieran a [EMAIL]"


@pytest.mark.parametrize("text,expected", [
    ("El cliente José Ángel Núñez García vive en la Calle Mayor 12, 3º B, 28013 Madrid.",
     "El cliente [PERSONA] vive en la [DIRECCION]."),
    ("Reunión con la abogada Lucía Fernández de Castro en Avenida Diagonal 640, Barcelona.",
     "Reunión con la abogada [PERSONA] en [DIRECCION]."),
])
def test_spanish_names_and_addresses(real_engine, policy, text, expected):
    assert real_engine.mask(text, policy).masked_text == expected


def test_no_false_positive_real(real_engine, policy):
    text = "Reunión el martes a las 10:00 en la sala 3."
    assert real_engine.mask(text, policy).masked_text == text


def test_long_document_real(real_engine, policy):
    filler = "El presente documento recoge las condiciones generales del servicio prestado. " * 40
    text = filler + "Firma: Pedro Ruiz Gómez, con DNI 12345678Z. " + filler
    r = real_engine.mask(text, policy)
    assert r.status.value == "masked"
    assert "Pedro" not in r.masked_text and "12345678Z" not in r.masked_text
    assert "Firma: [PERSONA], con DNI [DNI]." in r.masked_text
    assert r.masked_text.endswith(filler)


@pytest.mark.xfail(strict=True, reason="falso positivo conocido: GLiNER marca 'El presente documento' "
                   "como PERSONA (0.51) al inicio de una ventana; sobre-enmascara, no filtra")
def test_boilerplate_not_masked_real(real_engine, policy):
    filler = "El presente documento recoge las condiciones generales del servicio prestado. " * 40
    text = filler + "Firma: Pedro Ruiz Gómez. " + filler
    assert "[PERSONA] recoge" not in real_engine.mask(text, policy).masked_text
