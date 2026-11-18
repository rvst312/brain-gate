"""Métricas operativas sin contenido.

Solo se registran latencias, tamaños, recuentos por categoría, estados y
versiones. Nunca texto original, fragmentos detectados ni contenido de solicitudes.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("brain_gate")

_ALLOWED = {
    "event", "integration", "brain", "destination", "policy_version", "detector_version",
    "status", "reason", "elapsed_ms", "chars", "fields", "counts", "http_status",
}


def emit(event: str, **fields: object) -> None:
    unknown = set(fields) - _ALLOWED
    if unknown:  # defensa en profundidad: no aceptar campos libres
        raise ValueError(f"campos de telemetría no permitidos: {sorted(unknown)}")
    log.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))
