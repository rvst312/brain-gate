"""Pasarela de salida: única ruta autorizada hacia proveedores externos.

Evalúa todos los campos con texto (sistema, mensajes del usuario, historial,
contexto recuperado, resultados y argumentos de herramientas, entradas de
embeddings), conserva la estructura y solo envía cuando todo el procesamiento
ha terminado con ``masked``. Ante revisión pendiente, fallo, formato sin
soporte, límite excedido o política no configurada bloquea la salida y
devuelve un estado controlado sin llamar al proveedor.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import httpx

from . import telemetry
from .engine import Engine, Status
from .policy import PolicyStore


# --------------------------------------------------------------------------- maskers

@dataclass
class MaskOutcome:
    status: str  # "masked" | "needs_review" | "error"
    masked_text: str | None = None
    error: str | None = None


class Masker(Protocol):
    def mask(self, text: str, policy_id: str) -> MaskOutcome: ...


class LocalMasker:
    """Motor en proceso. Valida que la integración puede usar la política."""

    def __init__(self, engine: Engine, policies: PolicyStore, allowed_policies: set[str]) -> None:
        self.engine, self.policies, self.allowed = engine, policies, allowed_policies

    def mask(self, text: str, policy_id: str) -> MaskOutcome:
        policy = self.policies.get(policy_id)
        if policy is None or policy_id not in self.allowed:
            return MaskOutcome("error", error="policy_forbidden")
        if len(text) > policy.max_chars:
            return MaskOutcome("error", error="too_large")
        try:
            r = self.engine.mask(text, policy)
        except Exception:
            return MaskOutcome("error", error="detector_failure")
        if r.status is Status.MASKED:
            return MaskOutcome("masked", r.masked_text)
        return MaskOutcome("needs_review")


class HttpMasker:
    """Cliente de ``POST /v1/mask``. Cualquier respuesta no esperada es un error."""

    def __init__(self, base_url: str, token: str, timeout_s: float = 6.0,
                 client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self.headers = {"Authorization": f"Bearer {token}"}

    def mask(self, text: str, policy_id: str) -> MaskOutcome:
        try:
            resp = self.client.post("/v1/mask", json={"text": text, "policy_id": policy_id},
                                    headers=self.headers)
            body = resp.json()
        except Exception:
            return MaskOutcome("error", error="masker_unreachable")
        if resp.status_code == 200 and body.get("status") == "masked" and isinstance(body.get("masked_text"), str):
            return MaskOutcome("masked", body["masked_text"])
        if resp.status_code == 200 and body.get("status") == "needs_review":
            return MaskOutcome("needs_review")
        return MaskOutcome("error", error=str(body.get("error", f"http_{resp.status_code}")))


# --------------------------------------------------------------------------- providers

class Provider(Protocol):
    def send(self, route: str, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class MockProvider:
    """Proveedor simulado: registra exactamente lo que habría salido."""

    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reply: Callable[[str, dict[str, Any]], dict[str, Any]] = lambda route, p: {"ok": True}

    def send(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((route, copy.deepcopy(payload)))
        return self.reply(route, payload)


# --------------------------------------------------------------------------- request walking

class Unsupported(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


PASSTHROUGH_TOP = {"model", "max_tokens", "temperature", "top_p", "stop", "tools", "tool_choice", "stream"}
MESSAGE_KEYS = {"role", "content", "name", "tool_call_id"}
ROLES = {"user", "assistant", "tool"}

# Una ruta es la secuencia de claves/índices hasta un str dentro del payload.
Path = tuple[str | int, ...]


def _collect_strings(obj: Any, path: Path, out: list[Path]) -> None:
    """Recorre argumentos de herramientas: todo str es candidato."""
    if isinstance(obj, str):
        out.append(path)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _collect_strings(v, path + (k,), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _collect_strings(v, path + (i,), out)
    elif obj is None or isinstance(obj, (bool, int, float)):
        pass
    else:
        raise Unsupported("unsupported_value")


def _collect_content(content: Any, path: Path, out: list[Path]) -> None:
    if isinstance(content, str):
        out.append(path)
        return
    if not isinstance(content, list):
        raise Unsupported("unsupported_content")
    for i, part in enumerate(content):
        p = path + (i,)
        if not isinstance(part, dict):
            raise Unsupported("unsupported_part")
        kind = part.get("type")
        if kind == "text" and set(part) <= {"type", "text"} and isinstance(part.get("text"), str):
            out.append(p + ("text",))
        elif kind == "tool_use" and set(part) <= {"type", "id", "name", "input"}:
            _collect_strings(part.get("input", {}), p + ("input",), out)
        elif kind == "tool_result" and set(part) <= {"type", "tool_use_id", "content", "is_error"}:
            _collect_content(part.get("content", ""), p + ("content",), out)
        else:
            # Imágenes, documentos, adjuntos y cualquier formato sin tratamiento definido.
            raise Unsupported("unsupported_part")


def collect_chat_fields(req: dict[str, Any]) -> list[Path]:
    out: list[Path] = []
    for key in req:
        if key not in PASSTHROUGH_TOP | {"system", "messages"}:
            raise Unsupported("unsupported_field")
    if "system" in req:
        _collect_content(req["system"], ("system",), out)
    messages = req.get("messages")
    if not isinstance(messages, list) or not messages:
        raise Unsupported("missing_messages")
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or not set(m) <= MESSAGE_KEYS or m.get("role") not in ROLES:
            raise Unsupported("unsupported_message")
        _collect_content(m.get("content"), ("messages", i, "content"), out)
    return out


def collect_embedding_fields(req: dict[str, Any]) -> list[Path]:
    if not set(req) <= {"model", "input", "dimensions"}:
        raise Unsupported("unsupported_field")
    inp = req.get("input")
    if isinstance(inp, str):
        return [("input",)]
    if isinstance(inp, list) and inp and all(isinstance(x, str) for x in inp):
        return [("input", i) for i in range(len(inp))]
    raise Unsupported("unsupported_input")


COLLECTORS = {"chat": collect_chat_fields, "embeddings": collect_embedding_fields}


def _get(obj: Any, path: Path) -> Any:
    for k in path:
        obj = obj[k]
    return obj


def _set(obj: Any, path: Path, value: Any) -> None:
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value


# --------------------------------------------------------------------------- gateway

@dataclass
class GatewayResult:
    status: str  # "sent" | "blocked"
    reason: str | None = None
    response: dict[str, Any] | None = None
    policy_id: str | None = None


class Gateway:
    def __init__(
        self,
        integration_id: str,
        masker: Masker,
        providers: dict[str, Provider],
        policy_map: dict[tuple[str, str], str],  # (cerebro, destino) -> policy_id
        max_request_chars: int = 200_000,
    ) -> None:
        self.integration_id = integration_id
        self.masker = masker
        self.providers = providers
        self.policy_map = policy_map
        self.max_request_chars = max_request_chars

    def _blocked(self, brain: str, destination: str, reason: str, policy_id: str | None = None,
                 **extra: Any) -> GatewayResult:
        telemetry.emit("gateway", integration=self.integration_id, brain=brain, destination=destination,
                       status="blocked", reason=reason, **extra)
        return GatewayResult("blocked", reason=reason, policy_id=policy_id)

    def send(self, brain: str, destination: str, route: str, request: dict[str, Any]) -> GatewayResult:
        t0 = time.perf_counter()
        provider = self.providers.get(destination)
        policy_id = self.policy_map.get((brain, destination))
        if provider is None:
            return self._blocked(brain, destination, "unknown_destination")
        if policy_id is None:
            return self._blocked(brain, destination, "no_policy")
        collector = COLLECTORS.get(route)
        if collector is None:
            return self._blocked(brain, destination, "unsupported_route", policy_id)
        try:
            paths = collector(request)
        except Unsupported as e:
            return self._blocked(brain, destination, e.reason, policy_id)

        total = sum(len(_get(request, p)) for p in paths)
        if total > self.max_request_chars:
            return self._blocked(brain, destination, "too_large", policy_id, chars=total)

        out = copy.deepcopy(request)
        for p in paths:
            outcome = self.masker.mask(_get(request, p), policy_id)
            if outcome.status == "needs_review":
                return self._blocked(brain, destination, "needs_review", policy_id, fields=len(paths))
            if outcome.status != "masked" or outcome.masked_text is None:
                return self._blocked(brain, destination, outcome.error or "masker_error", policy_id)
            _set(out, p, outcome.masked_text)

        # Solo aquí, con todo procesado, se produce la salida externa.
        response = provider.send(route, out)
        telemetry.emit("gateway", integration=self.integration_id, brain=brain, destination=destination,
                       status="sent", fields=len(paths), chars=total,
                       elapsed_ms=round((time.perf_counter() - t0) * 1000, 2))
        return GatewayResult("sent", response=response, policy_id=policy_id)
