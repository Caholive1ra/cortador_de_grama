"""Testes da API FastAPI (health-check e validação de caminho)."""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_check() -> None:
    resposta = client.get("/health")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}


def test_cors_libera_origem_do_plugin() -> None:
    resposta = client.options(
        "/process",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resposta.status_code in (200, 204)
    assert resposta.headers.get("access-control-allow-origin") == "*"


def test_process_audio_file_not_found() -> None:
    caminho_inexistente = "C:\\caminho\\inexistente\\aula.mp4"
    resposta = client.post("/process", json={"pgm_path": caminho_inexistente})
    assert resposta.status_code == 404
    detalhe = resposta.json()["detail"]
    assert "não encontrado" in detalhe.lower()
    assert caminho_inexistente in detalhe


def test_process_exige_pgm() -> None:
    resposta = client.post(
        "/process",
        json={"camera_1_path": "C:\\aulas\\camera1.mp4"},
    )
    assert resposta.status_code == 422
