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
