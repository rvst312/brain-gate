"""Tipos básicos de detección.

Las posiciones son índices de puntos de código Unicode sobre el texto original:
``start`` inclusivo, ``end`` exclusivo. En Python coinciden con los índices de ``str``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    category: str
    score: float = 1.0
    source: str = "rules"

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("span vacío o negativo")

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end

    def shifted(self, offset: int) -> "Span":
        return Span(self.start + offset, self.end + offset, self.category, self.score, self.source)

    @property
    def length(self) -> int:
        return self.end - self.start
