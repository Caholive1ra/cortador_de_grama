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


def test_avaliacao_incompleta_nunca_publica(monkeypatch):
    monkeypatch.setenv("CLOUD_LEARNING_INSTALLATION_TOKEN", "segredo")
    calls = []
    def fake_supabase(method, table, **kwargs):
        calls.append((method, table, kwargs))
        return [{"id": "v1", "profile_id": "videoaula_editorial_v1", "configuration": {"rules": []}}] if table == "prompt_versions" and method == "GET" else []
    monkeypatch.setattr(cloud_worker, "_supabase", fake_supabase)
    response = TestClient(cloud_worker.app).post("/v1/prompt-versions/v1/evaluation", headers={"X-Installation-Token": "segredo"}, json={
        "reexecution_complete": False, "benchmark_samples": 99,
    })
    assert response.json() == {"approved": False, "status": "incomplete"}
    assert not any(table == "deployment_versions" for _, table, _ in calls)
