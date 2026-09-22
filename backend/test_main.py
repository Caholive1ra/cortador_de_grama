"""Testes da API FastAPI (health-check e validação de caminho)."""

from fastapi.testclient import TestClient
from contextlib import nullcontext

import pytest

from main import app
from audio_sync import SyncError, SyncResult
from transcriber import ModelUnavailableError

client = TestClient(app)


@pytest.mark.parametrize(
    "duracao_camera,status", [(1435.402035, 200), (1430.0, 200), (1500.0, 200)]
)
def test_validacao_duracao_camera(monkeypatch, duracao_camera, status):
    monkeypatch.setattr(
        "main.obter_metadados",
        lambda path: {
            "duration": 1435.435402 if path == "pgm.mp4" else duracao_camera,
            "fps": 29.97,
            "path": path,
        },
    )
    monkeypatch.setattr("main.extrair_audio_temporario", lambda *args: nullcontext("audio.wav"))
    monkeypatch.setattr("main.sincronizar_audio", lambda *args: SyncResult(2.0, 0.99, 0.0, 5))
    monkeypatch.setattr("main.transcrever_audio", lambda path: [])
    monkeypatch.setattr("main.classificar_segmentos", lambda *args, **kwargs: [])
    monkeypatch.setattr("main.gerar_fcp_xml", lambda *args, **kwargs: "resultado.xml")
    resposta = client.post(
        "/process", json={"pgm_path": "pgm.mp4", "camera_1_path": "cam.mp4"}
    )
    assert resposta.status_code == status
    assert resposta.json()["synchronization"][0]["offset_seconds"] == 2.0


def test_sync_insegura_interrompe_antes_da_transcricao(monkeypatch) -> None:
    monkeypatch.setattr("main.obter_metadados", lambda path: {
        "path": path, "duration": 60, "fps": 30,
    })
    monkeypatch.setattr("main.extrair_audio_temporario", lambda *args: nullcontext("audio.wav"))
    def falhar(*args):
        raise SyncError("Audio ambiguo")
    monkeypatch.setattr("main.sincronizar_audio", falhar)
    def nao_transcrever(*args):
        pytest.fail("Nao deve transcrever apos falha de sincronizacao")
    monkeypatch.setattr("main.transcrever_audio", nao_transcrever)
    resposta = client.post("/process", json={"pgm_path": "pgm.mp4", "camera_1_path": "cam.mp4"})
    assert resposta.status_code == 422
    assert "cam.mp4" in resposta.json()["detail"]
    assert "Audio ambiguo" in resposta.json()["detail"]


def test_health_check() -> None:
    resposta = client.get("/health")
    assert resposta.status_code == 200
    assert resposta.json() == {
        "status": "ok", "revision": "diretor-cameras-audio-v15"
    }


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


def test_modelo_indisponivel_retorna_503(monkeypatch) -> None:
    monkeypatch.setattr("main.obter_metadados", lambda path: {
        "path": path, "duration": 60, "fps": 30,
    })
    monkeypatch.setattr("main.extrair_audio_temporario", lambda *args: nullcontext("audio.wav"))
    monkeypatch.setattr("main.transcrever_audio", lambda path: (_ for _ in ()).throw(ModelUnavailableError("modelo ausente")))
    resposta = client.post("/process", json={"pgm_path": "pgm.mp4"})
    assert resposta.status_code == 503
    assert resposta.json()["detail"] == "modelo ausente"


def test_process_sincroniza_midias_e_gera_xml_com_lacunas(tmp_path, monkeypatch) -> None:
    """FFmpeg, correlacao e XML reais; somente a transcricao e substituida."""
    import shutil
    import subprocess
    import xml.etree.ElementTree as ET
    from test_audio_sync import _sinal, _wav, RATE

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/ffprobe nao instalados")
    sinal = _sinal()
    paths = []
    for nome, audio in (("pgm", sinal), ("cam", sinal[5 * RATE:45 * RATE])):
        wav = _wav(tmp_path / f"{nome}.wav", audio)
        video = tmp_path / f"{nome}.mkv"
        subprocess.run([
            "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", wav,
            "-f", "lavfi", "-i", "color=size=32x32:rate=30",
            "-c:v", "ffv1", "-c:a", "pcm_s16le", "-shortest", str(video),
        ], check=True, capture_output=True)
        paths.append(str(video))
    monkeypatch.setattr("main.transcrever_audio", lambda path: [
        {"start": 0.0, "end": 60.0, "text": "Uma explicacao completa."}
    ])
    monkeypatch.setattr("main.classificar_segmentos", lambda segmentos, **kwargs: [
        {"start": 0.0, "end": 60.0, "enabled": True, "text": segmentos[0]["text"]}
    ])
    resposta = client.post("/process", json={"pgm_path": paths[0], "camera_1_path": paths[1]})
    assert resposta.status_code == 200, resposta.text
    dados = resposta.json()
    assert dados["synchronization"][0]["offset_seconds"] == pytest.approx(5.0, abs=0.001)
    root = ET.parse(dados["xml_path"])
    tracks = root.findall("./sequence/media/video/track")
    cam = tracks[1].find("clipitem")
    assert [cam.findtext(k) for k in ("start", "end", "in", "out")] == ["150", "1350", "0", "1200"]
    assert root.findtext("./sequence/duration") == "1800"
