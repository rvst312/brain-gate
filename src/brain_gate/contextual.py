"""Detector contextual (nombres, direcciones) sobre ventanas solapadas.

El adaptador GLiNER carga los pesos exclusivamente desde disco y fuerza el modo
sin conexión de Hugging Face, de modo que el procesamiento no necesita salida a
internet. Cualquier modelo con ``predict_entities(text, labels, threshold)``
(misma firma que GLiNER) puede usarse, lo que permite probar la lógica de
ventanas sin cargar pesos.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .spans import Span


class ContextualModel(Protocol):
    def predict_entities(self, text: str, labels: list[str], threshold: float = ...) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Window:
    start: int
    text: str


_BOUNDARY = re.compile(r"[.!?;:\n]\s+|\n")


def make_windows(text: str, size: int = 1200, overlap: int = 200) -> list[Window]:
    """Divide el texto en ventanas solapadas, cortando en fin de frase si es posible.

    El solapamiento garantiza que una entidad más corta que ``overlap`` aparece
    completa en al menos una ventana.
    """
    if overlap >= size:
        raise ValueError("overlap debe ser menor que size")
    if len(text) <= size:
        return [Window(0, text)]
    windows: list[Window] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Buscar el último límite de frase en la mitad final de la ventana.
            floor = start + size // 2
            cut = None
            for m in _BOUNDARY.finditer(text, floor, end):
                cut = m.end()
            if cut is None:
                ws = text.rfind(" ", floor, end)
                cut = ws + 1 if ws > floor else end
            end = cut
        windows.append(Window(start, text[start:end]))
        if end >= len(text):
            break
        nxt = max(end - overlap, start + 1)
        # Evitar empezar a mitad de palabra.
        ws = text.find(" ", nxt, end)
        start = ws + 1 if ws != -1 else nxt
    return windows


class GlinerDetector:
    """GLiNER Multi PII v1 cargado desde una ruta local."""

    name = "gliner"

    def __init__(
        self,
        model: ContextualModel,
        labels: dict[str, str],
        threshold: float = 0.4,
        window_size: int = 1200,
        window_overlap: int = 200,
        version: str = "gliner-pii@1",
    ) -> None:
        self.model = model
        self.labels = labels  # etiqueta del modelo -> categoría de política
        self.threshold = threshold
        self.window_size = window_size
        self.window_overlap = window_overlap
        self.version = version

    @classmethod
    def from_local_path(cls, path: str, labels: dict[str, str], **kwargs: Any) -> "GlinerDetector":
        if not os.path.isdir(path):
            raise FileNotFoundError(f"no existe el directorio de pesos: {path}")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from gliner import GLiNER  # importación diferida: dependencia opcional

        # El config publicado nombra el backbone por su id del Hub. Si junto a los
        # pesos hay un ``backbone/`` con config y tokenizador, se usa ese directorio
        # para que la carga no dependa de la red ni de la caché de Hugging Face.
        import json
        from pathlib import Path

        model_dir = Path(path).resolve()
        backbone = model_dir / "backbone"
        config_dict = json.loads((model_dir / "gliner_config.json").read_text(encoding="utf-8"))
        # ``GLiNER`` es una fábrica: resolvemos la clase concreta igual que ella.
        concrete = GLiNER._get_gliner_class(GLiNER._config_from_dict(config_dict))
        loader = concrete
        if (backbone / "config.json").is_file():
            class _LocalBackbone(concrete):  # type: ignore[misc, valid-type]
                @classmethod
                def _load_config(klass, *a: Any, **k: Any):  # type: ignore[override]
                    config = super()._load_config(*a, **k)
                    config.model_name = str(backbone)
                    return config

            loader = _LocalBackbone
        model = loader.from_pretrained(model_id=str(model_dir), model_dir=str(model_dir), local_files_only=True)
        model.eval()
        return cls(model, labels, **kwargs)

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = []
        model_labels = list(self.labels)
        for w in make_windows(text, self.window_size, self.window_overlap):
            for ent in self.model.predict_entities(w.text, model_labels, threshold=self.threshold):
                category = self.labels.get(ent["label"])
                if category is None or ent["end"] <= ent["start"]:
                    continue
                spans.append(
                    Span(w.start + ent["start"], w.start + ent["end"], category, float(ent["score"]), self.name)
                )
        return spans
