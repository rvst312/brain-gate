"""Políticas versionadas.

La política define categorías, acción por categoría, excepciones, prioridad de
detecciones y tratamiento de dudas. El motor solo aplica esta configuración.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


BUILTIN_POLICY_DIR = Path(__file__).parent / "policies"


class Action(str, Enum):
    MASK = "mask"
    KEEP = "keep"
    REVIEW = "review"


class CategoryRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action = Action.MASK
    placeholder: str | None = None
    # Por debajo de este umbral la detección se considera dudosa.
    min_score: float = 0.5
    # Tratamiento de dudas de esta categoría; si falta, se usa el de la política.
    on_doubt: Action | None = None
    # Valores literales que se conservan (p. ej. el correo público de la empresa).
    exceptions: list[str] = Field(default_factory=list)
    # Token único reconstruible en lugar del placeholder fijo; si falta, se usa el de la política.
    reversible: bool | None = None


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: int
    language: str = "es"
    categories: dict[str, CategoryRule]
    # Orden de prioridad para resolver solapamientos entre categorías (primero gana).
    priority: list[str]
    # Qué hacer con una detección dudosa (puntuación < min_score).
    on_doubt: Action = Action.REVIEW
    # Detecciones de categorías no declaradas en la política.
    on_unknown_category: Action = Action.MASK
    max_chars: int = 25_000
    # Sustituir por tokens únicos ``[CATEGORIA_<hex>]`` que permiten reconstruir el dato.
    reversible: bool = False

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @model_validator(mode="after")
    def _check_priority(self) -> "Policy":
        missing = set(self.categories) - set(self.priority)
        if missing:
            raise ValueError(f"categorías sin prioridad: {sorted(missing)}")
        return self

    def rule(self, category: str) -> CategoryRule:
        return self.categories.get(category) or CategoryRule(action=self.on_unknown_category)

    def placeholder(self, category: str) -> str:
        return self.rule(category).placeholder or f"[{category}]"

    def is_reversible(self, category: str) -> bool:
        own = self.rule(category).reversible
        return self.reversible if own is None else own

    def rank(self, category: str) -> int:
        try:
            return self.priority.index(category)
        except ValueError:
            return len(self.priority)


class PolicyStore:
    def __init__(self, policies: dict[str, Policy]) -> None:
        self._policies = policies

    @classmethod
    def default(cls) -> "PolicyStore":
        """Políticas incluidas en el paquete, o ``BRAIN_GATE_POLICY_DIR`` si está definida."""
        import os
        return cls.from_dir(os.environ.get("BRAIN_GATE_POLICY_DIR") or BUILTIN_POLICY_DIR)

    @classmethod
    def from_dir(cls, path: str | Path) -> "PolicyStore":
        policies: dict[str, Policy] = {}
        for f in sorted(Path(path).glob("*.json")):
            p = Policy.model_validate(json.loads(f.read_text(encoding="utf-8")))
            policies[p.id] = p
        return cls(policies)

    def get(self, policy_id: str) -> Policy | None:
        return self._policies.get(policy_id)

    def ids(self) -> list[str]:
        return list(self._policies)
