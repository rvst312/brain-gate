"""La CLI y los casos de uso de examples/ funcionan y no dejan pasar datos sensibles."""

import json
import os
import subprocess
import sys

import pytest

from brain_gate.cli import main
from conftest import ROOT

RULE_PII = ["12345678Z", "ES91 2100 0418 4502 0005 1332", "maria.lopez@example.com", "612 345 678", "+34 912 34 56 78"]


def test_cli_mask_rules_only(capsys):
    assert main(["mask", "--gliner", "none", "Escribe a ana@example.com o al 612 345 678"]) == 0
    assert capsys.readouterr().out == "Escribe a [EMAIL] o al [TELEFONO]\n"


def test_cli_spans_json(capsys):
    assert main(["mask", "--gliner", "none", "--spans", "DNI 12345678Z"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "masked" and out["masked_text"] == "DNI [DNI]"
    assert out["spans"] == [{"start": 4, "end": 13, "category": "DNI", "action": "mask"}]


def test_cli_needs_review_exit_code(capsys):
    assert main(["mask", "--gliner", "none", "DNI 12345678A"]) == 3
    assert capsys.readouterr().out == ""


def test_cli_unknown_policy(capsys):
    assert main(["mask", "--gliner", "none", "--policy", "nope", "x"]) == 2


def run_example(name: str) -> str:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run([sys.executable, str(ROOT / "examples" / name)], capture_output=True, text=True,
                          env=env, timeout=600, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.mark.slow
def test_example_rag_chat_egress():
    out = run_example("01_rag_chat_egress.py")
    assert "Resultado: sent" in out
    assert "blocked (unsupported_part). Envíos al proveedor: 1" in out
    sent = out.split("Lo que recibe el proveedor externo:")[1].split("Con una imagen adjunta")[0]
    for v in RULE_PII:
        assert v not in sent, v


@pytest.mark.slow
def test_example_index_masking():
    out = run_example("02_index_masking.py")
    assert "Documentos en revisión: ['doc_" in out  # el DNI con letra incorrecta queda apartado
    assert "Buscar '12345678Z' -> []" in out
    index_part = out.split("Buscar")[0]
    for v in RULE_PII:
        assert v not in index_part, v
