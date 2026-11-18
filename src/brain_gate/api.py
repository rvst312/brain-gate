"""API privada ``POST /v1/mask``.

Configuración por variables de entorno:

- ``BRAIN_GATE_POLICY_DIR``: directorio de políticas JSON (por defecto, las incluidas en el paquete).
- ``BRAIN_GATE_INTEGRATIONS``: JSON con las integraciones autorizadas::

      {"legal-brain-gateway": {"token_sha256": "<hex>", "policies": ["brain-external-es"]}}

- ``BRAIN_GATE_GLINER_PATH``: ruta local de los pesos GLiNER fijados. Si se define
  y no cargan, el servicio no se declara disponible (no degrada en silencio).
- ``BRAIN_GATE_GLINER_THRESHOLD`` (0.4): corte del detector. Entre este valor y el
  ``min_score`` de la política la detección es dudosa y se aplica ``on_doubt``.
- ``BRAIN_GATE_MAX_CONCURRENCY`` (4), ``BRAIN_GATE_TIMEOUT_S`` (5).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import telemetry
from .contextual import GlinerDetector
from .engine import Engine, MaskResult, Status
from .policy import PolicyStore
from .rules import RuleDetector

GLINER_LABELS = {
    "person": "PERSONA",
    "full name": "PERSONA",
    "address": "DIRECCION",
    "street address": "DIRECCION",
}


class MaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    policy_id: str
    include_spans: bool = False


@dataclass
class Integration:
    id: str
    token_sha256: str
    policies: set[str]


@dataclass
class ServiceState:
    engine: Engine | None = None
    policies: PolicyStore | None = None
    integrations: dict[str, Integration] = field(default_factory=dict)
    semaphore: asyncio.Semaphore | None = None
    timeout_s: float = 5.0
    ready: bool = False

    def authenticate(self, authorization: str | None) -> Integration | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        for integ in self.integrations.values():
            if hmac.compare_digest(digest, integ.token_sha256):
                return integ
        return None


def _error(http_status: int, code: str) -> JSONResponse:
    # Nunca incluir texto de entrada ni parcial en errores.
    return JSONResponse({"status": "error", "error": code}, status_code=http_status)


def load_integrations(raw: str | None) -> dict[str, Integration]:
    data = json.loads(raw) if raw else {}
    return {
        k: Integration(k, v["token_sha256"].lower(), set(v.get("policies", [])))
        for k, v in data.items()
    }


def build_engine(gliner_path: str | None = None, threshold: float | None = None) -> Engine:
    """Reglas + GLiNER (si se indica ruta). Sin ruta: solo reglas."""
    detectors: list = [RuleDetector()]
    if gliner_path:
        th = threshold if threshold is not None else float(os.environ.get("BRAIN_GATE_GLINER_THRESHOLD", "0.4"))
        detectors.append(GlinerDetector.from_local_path(gliner_path, GLINER_LABELS, threshold=th))
    return Engine(detectors)


def build_default_engine() -> Engine:
    return build_engine(os.environ.get("BRAIN_GATE_GLINER_PATH"))


def create_app(
    engine: Engine | None = None,
    policies: PolicyStore | None = None,
    integrations: dict[str, Integration] | None = None,
    max_concurrency: int | None = None,
    timeout_s: float | None = None,
) -> FastAPI:
    state = ServiceState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # El modelo se carga una vez por proceso, antes de declararse disponible.
        state.policies = policies or PolicyStore.default()
        state.integrations = integrations if integrations is not None else load_integrations(
            os.environ.get("BRAIN_GATE_INTEGRATIONS"))
        state.semaphore = asyncio.Semaphore(max_concurrency or int(os.environ.get("BRAIN_GATE_MAX_CONCURRENCY", "4")))
        state.timeout_s = timeout_s or float(os.environ.get("BRAIN_GATE_TIMEOUT_S", "5"))
        state.engine = engine or await asyncio.to_thread(build_default_engine)
        state.ready = True
        yield
        state.ready = False

    app = FastAPI(title="brain-gate", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.svc = state

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, __: RequestValidationError) -> JSONResponse:
        # El manejador por defecto de FastAPI devuelve la entrada; aquí no.
        return _error(422, "invalid_request")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, __: Exception) -> JSONResponse:
        return _error(500, "processing_error")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        if not state.ready:
            return _error(503, "not_ready")
        return JSONResponse({"status": "ready", "detector_version": state.engine.detector_version})

    @app.post("/v1/mask")
    async def mask(req: MaskRequest, authorization: str | None = Header(default=None)) -> JSONResponse:
        if not state.ready:
            return _error(503, "not_ready")
        integ = state.authenticate(authorization)
        if integ is None:
            return _error(401, "unauthorized")
        policy = state.policies.get(req.policy_id)
        if policy is None or req.policy_id not in integ.policies:
            telemetry.emit("mask", integration=integ.id, status="error", reason="policy_forbidden")
            return _error(403, "policy_forbidden")
        if len(req.text) > policy.max_chars:
            telemetry.emit("mask", integration=integ.id, policy_version=policy.ref, status="error",
                           reason="too_large", chars=len(req.text))
            return _error(413, "too_large")

        try:
            async with state.semaphore:
                result: MaskResult = await asyncio.wait_for(
                    asyncio.to_thread(state.engine.mask, req.text, policy), state.timeout_s)
        except TimeoutError:
            telemetry.emit("mask", integration=integ.id, policy_version=policy.ref, status="error",
                           reason="timeout", chars=len(req.text))
            return _error(504, "timeout")
        except Exception:
            telemetry.emit("mask", integration=integ.id, policy_version=policy.ref, status="error",
                           reason="detector_failure", chars=len(req.text))
            return _error(500, "processing_error")

        telemetry.emit("mask", integration=integ.id, policy_version=result.policy_version,
                       detector_version=result.detector_version, status=result.status.value,
                       elapsed_ms=round(result.elapsed_ms, 2), chars=len(req.text), counts=dict(result.counts))

        body: dict = {
            "status": result.status.value,
            "policy_version": result.policy_version,
            "detector_version": result.detector_version,
        }
        if result.status is Status.MASKED:
            body["masked_text"] = result.masked_text
        if req.include_spans:
            body["spans"] = [
                {"start": d.span.start, "end": d.span.end, "category": d.span.category, "action": d.action.value}
                for d in result.decisions
            ]
        return JSONResponse(body)

    return app


def app_from_env() -> FastAPI:  # uvicorn brain_gate.api:app_from_env --factory
    return create_app()


__all__ = ["create_app", "app_from_env", "Integration", "MaskRequest"]
