"""Testes da API FastAPI (health-check e validação de caminho)."""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_check() -> None:
    resposta = client.get("/health")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}


def test_process_audio_file_not_found() -> None:
    caminho_inexistente = "C:\\caminho\\inexistente\\aula.wav"
    resposta = client.post("/process", json={"file_path": caminho_inexistente})
    assert resposta.status_code == 404
    detalhe = resposta.json()["detail"]
    assert "não encontrado" in detalhe.lower()
    assert caminho_inexistente in detalhe
