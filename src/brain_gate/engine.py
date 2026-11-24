"""Motor: unifica detecciones, resuelve solapamientos y sustituye sobre el original."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from .policy import Action, Policy
from .spans import Span
from .vault import Vault


class Detector(Protocol):
    name: str
    version: str

    def detect(self, text: str) -> list[Span]: ...


class Status(str, Enum):
    MASKED = "masked"
    NEEDS_REVIEW = "needs_review"


@dataclass
class Decision:
    span: Span
    action: Action


@dataclass
class MaskResult:
    status: Status
    masked_text: str | None
    decisions: list[Decision]
    policy_version: str
    detector_version: str
    elapsed_ms: float = 0.0
    counts: Counter = field(default_factory=Counter)


def merge_same_category(spans: list[Span]) -> list[Span]:
    """Une detecciones solapadas o contiguas de la misma categoría (duplicados entre
    detectores, entidades partidas entre ventanas)."""
    out: list[Span] = []
    by_cat: dict[str, list[Span]] = {}
    for s in spans:
        by_cat.setdefault(s.category, []).append(s)
    for cat, items in by_cat.items():
        items.sort(key=lambda s: (s.start, -s.end))
        cur = items[0]
        for s in items[1:]:
            if s.start <= cur.end:
                cur = Span(cur.start, max(cur.end, s.end), cat, max(cur.score, s.score),
                           cur.source if cur.source == s.source else f"{cur.source}+{s.source}")
            else:
                out.append(cur)
                cur = s
        out.append(cur)
    return out


def resolve_overlaps(spans: list[Span], policy: Policy) -> list[Span]:
    """Entre categorías gana la de mayor prioridad (luego la más larga, luego la de
    mayor puntuación). Si el perdedor sobresale del ganador, el ganador se extiende
    a la unión: preferimos sobre-enmascarar a dejar escapar una parte."""
    ordered = sorted(spans, key=lambda s: (policy.rank(s.category), -s.length, -s.score, s.start))
    winners: list[Span] = []
    for s in ordered:
        clash = [i for i, w in enumerate(winners) if w.overlaps(s)]
        if not clash:
            winners.append(s)
            continue
        for i in clash:
            w = winners[i]
            winners[i] = Span(min(w.start, s.start), max(w.end, s.end), w.category, w.score, w.source)
    # Las extensiones pueden haber creado nuevos solapes entre ganadores.
    winners.sort(key=lambda s: s.start)
    merged: list[Span] = []
    for w in winners:
        if merged and w.start < merged[-1].end:
            prev = merged[-1]
            keep = prev if policy.rank(prev.category) <= policy.rank(w.category) else w
            merged[-1] = Span(prev.start, max(prev.end, w.end), keep.category, keep.score, keep.source)
        else:
            merged.append(w)
    return merged


def decide(span: Span, text: str, policy: Policy) -> Action:
    rule = policy.rule(span.category)
    if text[span.start:span.end] in rule.exceptions:
        return Action.KEEP
    if rule.action is Action.KEEP:
        return Action.KEEP
    if span.score < rule.min_score:
        return rule.on_doubt or policy.on_doubt
    return rule.action


def apply(text: str, decisions: list[Decision], policy: Policy, vault: Vault | None = None) -> str:
    """Con ``vault``, las categorías reversibles de la política reciben un token único
    reconstruible; el resto, su placeholder fijo."""
    parts: list[str] = []
    cursor = 0
    for d in sorted(decisions, key=lambda d: d.span.start):
        if d.action is not Action.MASK:
            continue
        parts.append(text[cursor:d.span.start])
        if vault is not None and policy.is_reversible(d.span.category):
            parts.append(vault.token(d.span.category, text[d.span.start:d.span.end]))
        else:
            parts.append(policy.placeholder(d.span.category))
        cursor = d.span.end
    parts.append(text[cursor:])
    return "".join(parts)


class Engine:
    def __init__(self, detectors: list[Detector]) -> None:
        if not detectors:
            raise ValueError("se necesita al menos un detector")
        self.detectors = detectors
        self.detector_version = "+".join(d.version for d in detectors)

    def mask(self, text: str, policy: Policy, vault: Vault | None = None) -> MaskResult:
        t0 = time.perf_counter()
        raw: list[Span] = []
        for det in self.detectors:
            raw.extend(det.detect(text))
        spans = resolve_overlaps(merge_same_category(raw), policy) if raw else []
        decisions = [Decision(s, decide(s, text, policy)) for s in spans]
        needs_review = any(d.action is Action.REVIEW for d in decisions)
        counts = Counter(f"{d.span.category}:{d.action.value}" for d in decisions)
        return MaskResult(
            status=Status.NEEDS_REVIEW if needs_review else Status.MASKED,
            masked_text=None if needs_review else apply(text, decisions, policy, vault),
            decisions=decisions,
            policy_version=policy.ref,
            detector_version=self.detector_version,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            counts=counts,
        )
