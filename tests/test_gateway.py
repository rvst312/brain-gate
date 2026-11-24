"""Límite de salida verificado contra un proveedor simulado."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from brain_gate.api import create_app
from brain_gate.engine import Engine
from brain_gate.gateway import Gateway, HttpMasker, LocalMasker, MockProvider

from conftest import TOKEN, FakeGliner, LABELS

POLICY_MAP = {("legal-brain", "llm"): "brain-external-es"}
PII = ["María López", "maria@example.com", "612 345 678", "12345678Z", "ES91 2100 0418 4502 0005 1332",
       "José Ángel Núñez", "Calle Mayor 12, 3º B, Madrid"]


@pytest.fixture
def provider():
    return MockProvider()


@pytest.fixture
def gw(engine, policies, provider):
    return Gateway("legal-brain-gateway", LocalMasker(engine, policies, {"brain-external-es"}), {"llm": provider}, POLICY_MAP)


def chat_request():
    return {
        "model": "x", "max_tokens": 512,
        "system": "Eres el asistente del equipo legal. Expediente de María López.",
        "messages": [
            {"role": "user", "content": "¿Qué dijo el cliente con DNI 12345678Z?"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Voy a buscar."},
                {"type": "tool_use", "id": "t1", "name": "buscar",
                 "input": {"query": "maria@example.com", "filtros": {"tel": "612 345 678"}, "limit": 5}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": [
                    {"type": "text", "text": "Doc: José Ángel Núñez, Calle Mayor 12, 3º B, Madrid. "
                                             "IBAN ES91 2100 0418 4502 0005 1332 🙂"}]},
                {"type": "text", "text": "Resume."},
            ]},
        ],
    }


def assert_no_pii(payload):
    dumped = json.dumps(payload, ensure_ascii=False)
    for v in PII:
        assert v not in dumped, v


def test_chat_end_to_end(gw, provider):
    r = gw.send("legal-brain", "llm", "chat", chat_request())
    assert r.status == "sent"
    ((route, sent),) = provider.sent
    assert route == "chat"
    assert_no_pii(sent)
    # Estructura conservada.
    assert sent["model"] == "x" and sent["max_tokens"] == 512
    assert sent["messages"][1]["content"][1]["input"] == {
        "query": "[EMAIL]", "filtros": {"tel": "[TELEFONO]"}, "limit": 5}
    assert sent["messages"][2]["content"][0]["content"][0]["text"] == (
        "Doc: [PERSONA], [DIRECCION]. IBAN [IBAN] 🙂")
    assert sent["system"] == "Eres el asistente del equipo legal. Expediente de [PERSONA]."


def test_original_request_untouched(gw):
    req = chat_request()
    before = json.dumps(req)
    gw.send("legal-brain", "llm", "chat", req)
    assert json.dumps(req) == before


def test_embeddings_route(gw, provider):
    r = gw.send("legal-brain", "llm", "embeddings", {"model": "e", "input": ["María López", "hola"]})
    assert r.status == "sent"
    assert provider.sent[0][1]["input"] == ["[PERSONA]", "hola"]


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r["messages"][0].update(content=[{"type": "image", "source": {"data": "..."}}]), "unsupported_part"),
    (lambda r: r["messages"][0].update(content=[{"type": "document", "source": {}}]), "unsupported_part"),
    (lambda r: r.update(metadata={"user": "María López"}), "unsupported_field"),
    (lambda r: r["messages"][0].update(attachments=["x.pdf"]), "unsupported_message"),
    (lambda r: r["messages"][0].update(role="system"), "unsupported_message"),
    (lambda r: r["messages"][0].update(content="DNI 12345678A"), "needs_review"),
    (lambda r: r["messages"][0].update(content="a" * 60_000), "too_large"),
])
def test_blocks_without_calling_provider(gw, provider, mutate, reason):
    req = chat_request()
    mutate(req)
    r = gw.send("legal-brain", "llm", "chat", req)
    assert (r.status, r.reason) == ("blocked", reason)
    assert provider.sent == []


def test_blocks_unknown_brain_destination_route(gw, provider):
    assert gw.send("otro-cerebro", "llm", "chat", chat_request()).reason == "no_policy"
    assert gw.send("legal-brain", "otro", "chat", chat_request()).reason == "unknown_destination"
    assert gw.send("legal-brain", "llm", "files", {}).reason == "unsupported_route"
    assert provider.sent == []


def test_detector_down_blocks(policies, provider):
    from brain_gate.contextual import GlinerDetector
    from brain_gate.rules import RuleDetector
    engine = Engine([RuleDetector(), GlinerDetector(FakeGliner({}, fail=True), LABELS)])
    gw = Gateway("legal-brain-gateway", LocalMasker(engine, policies, {"brain-external-es"}), {"llm": provider}, POLICY_MAP)
    r = gw.send("legal-brain", "llm", "chat", chat_request())
    assert (r.status, r.reason) == ("blocked", "detector_failure") and provider.sent == []


def test_policy_not_authorized_for_integration(engine, policies, provider):
    gw = Gateway("legal-brain-gateway", LocalMasker(engine, policies, set()), {"llm": provider}, POLICY_MAP)
    assert gw.send("legal-brain", "llm", "chat", chat_request()).reason == "policy_forbidden"
    assert provider.sent == []


# ---- pasarela contra el servicio HTTP real (en proceso)

def test_via_http_service(engine, policies, integrations, provider):
    with TestClient(create_app(engine=engine, policies=policies, integrations=integrations)) as svc:
        gw = Gateway("legal-brain-gateway", HttpMasker("http://svc", TOKEN, client=svc), {"llm": provider}, POLICY_MAP)
        r = gw.send("legal-brain", "llm", "chat", chat_request())
    assert r.status == "sent"
    assert_no_pii(provider.sent[0][1])


def test_http_service_unreachable_blocks(provider):
    def boom(request):
        raise httpx.ConnectError("caído")
    client = httpx.Client(base_url="http://svc", transport=httpx.MockTransport(boom))
    gw = Gateway("legal-brain-gateway", HttpMasker("http://svc", TOKEN, client=client), {"llm": provider}, POLICY_MAP)
    r = gw.send("legal-brain", "llm", "chat", chat_request())
    assert (r.status, r.reason) == ("blocked", "masker_unreachable") and provider.sent == []


def test_http_service_timeout_blocks(provider):
    def slow(request):
        return httpx.Response(504, json={"status": "error", "error": "timeout"})
    client = httpx.Client(base_url="http://svc", transport=httpx.MockTransport(slow))
    gw = Gateway("legal-brain-gateway", HttpMasker("http://svc", TOKEN, client=client), {"llm": provider}, POLICY_MAP)
    assert gw.send("legal-brain", "llm", "chat", chat_request()).reason == "timeout"
    assert provider.sent == []


# ---- reconstrucción del dato

REV = "brain-external-es-reversible"
REV_MAP = {("legal-brain", "llm"): REV}


def echo_reply(route, payload):
    """El modelo cita lo recibido y pide una herramienta con los mismos tokens."""
    doc = payload["messages"][2]["content"][0]["content"][0]["text"]
    return {"role": "assistant", "content": [
        {"type": "text", "text": f"Sistema: {payload['system']} | Doc: {doc}"},
        {"type": "tool_use", "id": "t2", "name": "enviar",
         "input": {"to": payload["messages"][1]["content"][1]["input"]["query"],
                   "dni": payload["messages"][0]["content"].split("DNI ")[1].rstrip("?"), "n": 1}},
    ], "usage": {"output_tokens": 12}}


def assert_roundtrip(r, provider):
    assert r.status == "sent"
    sent = provider.sent[0][1]
    assert_no_pii(sent)
    dumped = json.dumps(sent, ensure_ascii=False)
    for cat in ("PERSONA", "DNI", "EMAIL", "TELEFONO", "DIRECCION"):
        assert f"[{cat}_" in dumped and f"[{cat}]" not in dumped
    assert "[IBAN]" in dumped and "[IBAN_" not in dumped
    text, tool = r.response["content"]
    assert text["text"] == ("Sistema: Eres el asistente del equipo legal. Expediente de María López. | "
                            "Doc: Doc: José Ángel Núñez, Calle Mayor 12, 3º B, Madrid. IBAN [IBAN] 🙂")
    assert tool["input"] == {"to": "maria@example.com", "dni": "12345678Z", "n": 1}
    assert r.response["usage"] == {"output_tokens": 12}
    assert r.restored == 5


def test_reversible_roundtrip_local(engine, policies):
    provider = MockProvider(reply=echo_reply)
    gw = Gateway("legal-brain-gateway", LocalMasker(engine, policies, {REV}), {"llm": provider}, REV_MAP)
    assert_roundtrip(gw.send("legal-brain", "llm", "chat", chat_request()), provider)


def test_reversible_roundtrip_http(engine, policies, integrations):
    integrations["legal-brain-gateway"].policies.add(REV)
    provider = MockProvider(reply=echo_reply)
    with TestClient(create_app(engine=engine, policies=policies, integrations=integrations)) as svc:
        gw = Gateway("legal-brain-gateway", HttpMasker("http://svc", TOKEN, client=svc), {"llm": provider}, REV_MAP)
        assert_roundtrip(gw.send("legal-brain", "llm", "chat", chat_request()), provider)


def test_same_value_same_token_across_fields_and_turns(engine, policies):
    provider = MockProvider()
    gw = Gateway("legal-brain-gateway", LocalMasker(engine, policies, {REV}), {"llm": provider}, REV_MAP)
    req = {"model": "x", "system": "Cliente: María López.",
           "messages": [{"role": "user", "content": "¿Qué pidió María López?"}]}
    gw.send("legal-brain", "llm", "chat", req)
    gw.send("legal-brain", "llm", "chat", req)
    (_, a), (_, b) = provider.sent
    token = a["system"].removeprefix("Cliente: ").removesuffix(".")
    assert token.startswith("[PERSONA_") and a["messages"][0]["content"] == f"¿Qué pidió {token}?"
    assert a == b
    # Otra instancia sin clave compartida genera tokens distintos; con la misma clave, iguales.
    other = MockProvider()
    Gateway("g", LocalMasker(engine, policies, {REV}), {"llm": other}, REV_MAP).send("legal-brain", "llm", "chat", req)
    assert other.sent[0][1] != a
    same = MockProvider()
    for p in (same, other):
        Gateway("g", LocalMasker(engine, policies, {REV}), {"llm": p}, REV_MAP,
                token_key=b"k").send("legal-brain", "llm", "chat", req)
    assert same.sent[0][1] == other.sent[1][1]


def test_restore_disabled_keeps_placeholders(engine, policies):
    provider = MockProvider()
    gw = Gateway("legal-brain-gateway", LocalMasker(engine, policies, {REV}), {"llm": provider}, REV_MAP, restore=False)
    r = gw.send("legal-brain", "llm", "chat", chat_request())
    assert r.restored == 0 and provider.sent[0][1]["system"].endswith("Expediente de [PERSONA].")


def test_embeddings_never_use_tokens(engine, policies):
    provider = MockProvider()
    gw = Gateway("legal-brain-gateway", LocalMasker(engine, policies, {REV}), {"llm": provider}, REV_MAP)
    gw.send("legal-brain", "llm", "embeddings", {"model": "e", "input": ["María López", "hola"]})
    assert provider.sent[0][1]["input"] == ["[PERSONA]", "hola"]


def test_non_reversible_policy_response_untouched(gw, provider):
    provider.reply = lambda route, p: {"content": [{"type": "text", "text": p["system"]}]}
    r = gw.send("legal-brain", "llm", "chat", chat_request())
    assert r.restored == 0 and r.response["content"][0]["text"].endswith("Expediente de [PERSONA].")


@pytest.mark.parametrize("mappings", [None, "x", {"[PERSONA]": "x"}, {"[PERSONA_ab12cd34ef]": 7}])
def test_http_invalid_mappings_block(provider, mappings):
    def handler(request):
        body = {"status": "masked", "masked_text": "hola"}
        if mappings is not None:
            body["mappings"] = mappings
        return httpx.Response(200, json=body)
    client = httpx.Client(base_url="http://svc", transport=httpx.MockTransport(handler))
    gw = Gateway("legal-brain-gateway", HttpMasker("http://svc", TOKEN, client=client), {"llm": provider}, POLICY_MAP)
    r = gw.send("legal-brain", "llm", "chat", chat_request())
    assert (r.status, r.reason) == ("blocked", "invalid_mappings") and provider.sent == []


def test_gateway_logs_have_no_content(engine, policies, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    provider = MockProvider(reply=echo_reply)
    gw = Gateway("legal-brain-gateway", LocalMasker(engine, policies, {REV}), {"llm": provider}, REV_MAP)
    gw.send("legal-brain", "llm", "chat", chat_request())
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert '"restored": 5' in joined
    for v in PII:
        assert v not in joined
