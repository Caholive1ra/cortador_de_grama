"""Regressoes de duracao de video versus duracao do container."""

import json
import shutil
import subprocess
import wave
from types import SimpleNamespace

import pytest

from media_utils import obter_metadados, extrair_audio_temporario


@pytest.mark.parametrize("duration", ["1435.435402", None, "N/A"])
def test_duracao_prioriza_faixa_de_video(tmp_path, monkeypatch, duration):
    video = tmp_path / "video.mp4"
    video.touch()
    stream = {"width": 1920, "height": 1080, "avg_frame_rate": "2997/100"}
    if duration is not None:
        stream["duration"] = duration
    payload = {"streams": [stream], "format": {"duration": "1435.498646"}}
    monkeypatch.setattr(
        "media_utils.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )
    dados = obter_metadados(str(video))
    esperado = 1435.435402 if duration == "1435.435402" else 1435.498646
    assert dados["duration"] == esperado


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg nao instalado")
def test_extracao_preserva_atraso_do_audio_e_limpa_temporario(tmp_path) -> None:
    import numpy as np
    video = tmp_path / "audio_atrasado.mkv"
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=size=32x32:rate=30:duration=2",
        "-itsoffset", "0.5", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "ffv1", "-c:a", "pcm_s16le", str(video),
    ], check=True, capture_output=True)
    with extrair_audio_temporario(str(video)) as audio_path:
        with wave.open(audio_path, "rb") as wav:
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        assert np.max(np.abs(audio[:7900])) == 0
        assert np.max(np.abs(audio[8100:16000])) > 100
    from pathlib import Path
    assert not Path(audio_path).exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg nao instalado")
def test_fonte_sem_audio_tem_erro_explicativo(tmp_path) -> None:
    video = tmp_path / "sem_audio.mkv"
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=size=32x32:rate=30:duration=1",
        "-c:v", "ffv1", str(video),
    ], check=True, capture_output=True)
    with pytest.raises(ValueError, match="faixa de audio valida"):
        with extrair_audio_temporario(str(video)):
            pytest.fail("Nao deveria extrair arquivo sem audio")
