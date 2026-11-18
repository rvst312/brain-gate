"""BrainGate: ofuscador local de datos sensibles para IA."""

from .api import build_engine, create_app
from .contextual import GlinerDetector
from .engine import Engine, MaskResult, Status
from .gateway import Gateway, GatewayResult, HttpMasker, LocalMasker, MockProvider, Provider
from .policy import Action, Policy, PolicyStore
from .rules import RuleDetector

__version__ = "0.1.0"

__all__ = [
    "Action", "Engine", "Gateway", "GatewayResult", "GlinerDetector", "HttpMasker", "LocalMasker",
    "MaskResult", "MockProvider", "Policy", "PolicyStore", "Provider", "RuleDetector", "Status",
    "build_engine", "create_app",
]
