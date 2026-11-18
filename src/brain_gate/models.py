"""Descarga de pesos con revisiones fijadas.

Es el único punto del proyecto que accede a la red, y solo cuando se invoca
explícitamente (``braingate download-model``). El procesamiento posterior
carga los pesos desde disco sin conexión.
"""

from __future__ import annotations

from pathlib import Path

GLINER_REPO = "urchade/gliner_multi_pii-v1"
GLINER_REVISION = "1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d"
BACKBONE_REPO = "microsoft/mdeberta-v3-base"
BACKBONE_REVISION = "a0484667b22365f84929a935b5e50a51f71f159d"
DEFAULT_MODEL_DIR = Path("models") / "gliner_multi_pii-v1"


def download_gliner(dest: str | Path = DEFAULT_MODEL_DIR) -> Path:
    from huggingface_hub import snapshot_download

    dest = Path(dest)
    snapshot_download(GLINER_REPO, revision=GLINER_REVISION, local_dir=dest,
                      allow_patterns=["gliner_config.json", "pytorch_model.bin", "README.md"])
    # Solo config y tokenizador del backbone: sus pesos ya van dentro de GLiNER.
    snapshot_download(BACKBONE_REPO, revision=BACKBONE_REVISION, local_dir=dest / "backbone",
                      allow_patterns=["config.json", "tokenizer_config.json", "spm.model"])
    return dest
