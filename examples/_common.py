"""Utilidades compartidas por los ejemplos: usa GLiNER si los pesos están en disco."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from brain_gate import build_engine  # noqa: E402

MODEL_DIR = ROOT / "models" / "gliner_multi_pii-v1"


def engine():
    if (MODEL_DIR / "pytorch_model.bin").is_file():
        print(f"· Detector: reglas + GLiNER ({MODEL_DIR.relative_to(ROOT)})\n")
        return build_engine(str(MODEL_DIR))
    print("· Detector: solo reglas. Para detectar nombres y direcciones: `uv run braingate download-model`\n")
    return build_engine(None)
