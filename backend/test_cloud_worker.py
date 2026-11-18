import os

from fastapi.testclient import TestClient

import cloud_worker


def test_worker_rejeita_instalacao_sem_token(monkeypatch):
    monkeypatch.setenv("CLOUD_LEARNING_INSTALLATION_TOKEN", "segredo")
    response = TestClient(cloud_worker.app).post("/v1/feedback", json={
        "event_id": "a" * 12, "idempotency_key": "b" * 32, "profile_id": "videoaula_editorial_v1",
        "project_type": "videoaula", "categories": [], "metrics": {}, "examples_count": 0,
    })
    assert response.status_code == 401
