import logging
import time

import pytest
from fastapi.testclient import TestClient

from brain_gate.api import create_app
from brain_gate.engine import Engine

from conftest import TOKEN

AUTH = {"Authorization": f"Bearer {TOKEN}"}
SECRET = "maria@example.com"


@pytest.fixture
def client(engine, policies, integrations):
    with TestClient(create_app(engine=engine, policies=policies, integrations=integrations)) as c:
        yield c


def test_ready(client):
    assert client.get("/readyz").json()["status"] == "ready"


def test_mask_contract(client):
    r = client.post("/v1/mask", headers=AUTH, json={
        "text": "María López pidió que le escribieran a maria@example.com",
        "policy_id": "brain-external-es", "include_spans": False})
    assert r.status_code == 200
    assert r.json() == {
        "status": "masked",
        "masked_text": "[PERSONA] pidió que le escribieran a [EMAIL]",
        "policy_version": "brain-external-es@3",
        "detector_version": "rules@1+gliner-pii@1",
    }


def test_spans_have_no_text(client):
    body = client.post("/v1/mask", headers=AUTH, json={
        "text": "😀 María López", "policy_id": "brain-external-es", "include_spans": True}).json()
    assert body["spans"] == [{"start": 2, "end": 13, "category": "PERSONA", "action": "mask"}]


def test_needs_review_omits_text(client):
    body = client.post("/v1/mask", headers=AUTH, json={
        "text": "DNI 12345678A", "policy_id": "brain-external-es"}).json()
    assert body["status"] == "needs_review" and "masked_text" not in body


def test_auth_and_policy_permission(client):
    assert client.post("/v1/mask", json={"text": "x", "policy_id": "brain-external-es"}).status_code == 401
    assert client.post("/v1/mask", headers={"Authorization": "Bearer otro"},
                       json={"text": "x", "policy_id": "brain-external-es"}).status_code == 401
    r = client.post("/v1/mask", headers=AUTH, json={"text": "x", "policy_id": "otra-politica"})
    assert r.status_code == 403 and r.json() == {"status": "error", "error": "policy_forbidden"}


def test_validation_error_does_not_echo_input(client):
    r = client.post("/v1/mask", headers=AUTH, json={"text": SECRET, "policy_id": 5, "extra": SECRET})
    assert r.status_code == 422 and SECRET not in r.text


def test_too_large(client):
    r = client.post("/v1/mask", headers=AUTH, json={"text": "a" * 25_001, "policy_id": "brain-external-es"})
    assert r.status_code == 413


def test_detector_failure_no_text_leak(policies, integrations):
    class Broken:
        name, version = "broken", "broken@1"
        def detect(self, text):
            raise RuntimeError(f"fallo procesando {text}")
    with TestClient(create_app(engine=Engine([Broken()]), policies=policies, integrations=integrations)) as c:
        r = c.post("/v1/mask", headers=AUTH, json={"text": SECRET, "policy_id": "brain-external-es"})
    assert r.status_code == 500 and SECRET not in r.text


def test_timeout(policies, integrations):
    class Slow:
        name, version = "slow", "slow@1"
        def detect(self, text):
            time.sleep(0.5)
            return []
    with TestClient(create_app(engine=Engine([Slow()]), policies=policies, integrations=integrations,
                               timeout_s=0.05)) as c:
        r = c.post("/v1/mask", headers=AUTH, json={"text": SECRET, "policy_id": "brain-external-es"})
    assert r.status_code == 504 and SECRET not in r.text


def test_logs_contain_no_content(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/v1/mask", headers=AUTH, json={
        "text": "María López, maria@example.com, 612 345 678", "policy_id": "brain-external-es"})
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert '"event": "mask"' in joined
    for secret in ("María", "López", SECRET, "612 345 678"):
        assert secret not in joined


# ---- tokens reversibles

REV = "brain-external-es-reversible"


@pytest.fixture
def rev_client(engine, policies, integrations):
    integrations["legal-brain-gateway"].policies.add(REV)
    with TestClient(create_app(engine=engine, policies=policies, integrations=integrations, token_key=b"k")) as c:
        yield c


def test_reversible_returns_mappings(rev_client):
    text = "María López pidió que le escribieran a maria@example.com. IBAN ES91 2100 0418 4502 0005 1332"
    body = rev_client.post("/v1/mask", headers=AUTH, json={"text": text, "policy_id": REV, "reversible": True}).json()
    assert body["status"] == "masked"
    assert sorted(body["mappings"].values()) == ["María López", "maria@example.com"]
    masked = body["masked_text"]
    assert masked.endswith("IBAN [IBAN]") and "María" not in masked and SECRET not in masked
    for token, original in body["mappings"].items():
        masked = masked.replace(token, original)
    assert masked == text.replace("ES91 2100 0418 4502 0005 1332", "[IBAN]")
    # Clave fija: mismos tokens en otra petición.
    again = rev_client.post("/v1/mask", headers=AUTH, json={"text": text, "policy_id": REV, "reversible": True}).json()
    assert again["mappings"] == body["mappings"]


def test_mappings_only_on_request(rev_client):
    body = rev_client.post("/v1/mask", headers=AUTH, json={"text": "María López", "policy_id": REV}).json()
    assert body["masked_text"] == "[PERSONA]" and "mappings" not in body


def test_reversible_on_plain_policy_gives_empty_mappings(client):
    body = client.post("/v1/mask", headers=AUTH, json={
        "text": "María López", "policy_id": "brain-external-es", "reversible": True}).json()
    assert body["masked_text"] == "[PERSONA]" and body["mappings"] == {}


def test_reversible_needs_review_has_no_mappings(rev_client):
    body = rev_client.post("/v1/mask", headers=AUTH, json={
        "text": "María López, DNI 12345678A", "policy_id": REV, "reversible": True}).json()
    assert body["status"] == "needs_review" and "mappings" not in body and "masked_text" not in body


def test_reversible_logs_contain_no_content(rev_client, caplog):
    caplog.set_level(logging.DEBUG)
    rev_client.post("/v1/mask", headers=AUTH, json={
        "text": "María López, maria@example.com", "policy_id": REV, "reversible": True})
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert '"event": "mask"' in joined and "María" not in joined and SECRET not in joined and "PERSONA_" not in joined
