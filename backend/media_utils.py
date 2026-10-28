"""Extração de metadados e áudio com FFmpeg/ffprobe."""

import json
import logging
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)
FFMPEG_TIMEOUT_SECONDS = int(os.getenv("FFMPEG_TIMEOUT_SECONDS", "3600"))


def _executar(comando: list[str]) -> subprocess.CompletedProcess[str]:
    """Executa utilitarios de midia com limite e mensagens acionaveis."""
    try:
        return subprocess.run(
            comando, capture_output=True, text=True, check=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{comando[0]} nao foi encontrado no PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{comando[0]} excedeu o tempo limite de {FFMPEG_TIMEOUT_SECONDS}s."
        ) from exc


def obter_metadados(file_path: str) -> dict:
    """Obtém duração, frame rate e dimensões da primeira faixa de vídeo."""
    caminho = Path(file_path)
    if not caminho.is_file():
        raise FileNotFoundError(f"Arquivo de mídia não encontrado: {file_path}")

    comando = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,duration,start_time:format=duration,start_time",
        "-of", "json", str(caminho),
    ]
    logger.info("Lendo metadados de: %s", file_path)
    resultado = _executar(comando)
    dados = json.loads(resultado.stdout)
    streams = dados.get("streams", [])
    if not streams:
        raise ValueError(f"Nenhuma faixa de vídeo encontrada: {file_path}")
    stream = streams[0]
    numerador, denominador = stream["avg_frame_rate"].split("/", 1)
    fps = float(numerador) / float(denominador)
    # A faixa de audio pode terminar depois do video no mesmo container.
    duracao_video = stream.get("duration")
    duracao = float(
        duracao_video
        if duracao_video not in (None, "N/A", "")
        else dados["format"]["duration"]
    )
    if fps <= 0 or duracao <= 0:
        raise ValueError(f"Metadados inválidos para: {file_path}")
    return {
        "path": str(caminho.resolve()),
        "duration": duracao,
        "fps": fps,
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "video_start": float(stream.get("start_time", 0) or 0),
    }


def obter_metadados_audio(file_path: str) -> dict:
    """Obtém a duração de uma fonte de áudio externa."""
    caminho = Path(file_path)
    if not caminho.is_file():
        raise FileNotFoundError(f"Arquivo de mídia não encontrado: {file_path}")
    comando = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=duration,start_time:format=duration,start_time",
        "-of", "json", str(caminho),
    ]
    logger.info("Lendo metadados de áudio: %s", file_path)
    resultado = _executar(comando)
    dados = json.loads(resultado.stdout)
    streams = dados.get("streams", [])
    if not streams:
        raise ValueError(f"Nenhuma faixa de áudio encontrada: {file_path}")
    stream = streams[0]
    duracao = float(
        stream.get("duration")
        if stream.get("duration") not in (None, "N/A", "")
        else dados["format"]["duration"]
    )
    if duracao <= 0:
        raise ValueError(f"Metadados de áudio inválidos para: {file_path}")
    return {
        "path": str(caminho.resolve()),
        "duration": duracao,
        "audio_start": float(stream.get("start_time", 0) or 0),
        "kind": "audio",
    }


@contextmanager
def extrair_audio_temporario(file_path: str, video_start: float = 0.0) -> Iterator[str]:
    """Extrai WAV mono/16 kHz e remove o arquivo ao final do processamento."""
    descritor, audio_path = tempfile.mkstemp(prefix="decupagem_", suffix=".wav")
    os.close(descritor)
    try:
        comando = [
            "ffmpeg", "-nostdin", "-y", "-v", "error", "-copyts", "-i", file_path,
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
            "-af", f"asetpts=PTS-({video_start:.9f})/TB,aresample=16000:first_pts=0",
            "-c:a", "pcm_s16le", audio_path,
        ]
        logger.info("Extraindo audio temporario: %s", file_path)
        try:
            _executar(comando)
        except subprocess.CalledProcessError as exc:
            logger.error("FFmpeg: %s", exc.stderr)
            raise ValueError(
                f"Nao foi possivel extrair o audio de {Path(file_path).name}. "
                "Verifique se o arquivo possui uma faixa de audio valida; "
                "fontes opcionais sem audio devem ser puladas."
            ) from exc
        yield audio_path
    finally:
        try:
            os.unlink(audio_path)
        except FileNotFoundError:
            pass


def aparar_silencios_das_bordas(audio_path: str, trechos: list[dict]) -> list[dict]:
    """Remove no máximo 200 ms de silêncio real em cada ponta de um take.

    Os limites vindos do Whisper são usados como guarda para que a detecção de
    silêncio nunca avance sobre a primeira ou a última palavra reconhecida.
    """
    resultado = []
    for trecho in trechos:
        inicio, fim = float(trecho["start"]), float(trecho["end"])
        duracao = fim - inicio
        comando = [
            "ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{inicio:.3f}", "-t", f"{duracao:.3f}",
            "-i", audio_path, "-af", "silencedetect=noise=-38dB:d=0.10", "-f", "null", "-",
        ]
        try:
            execucao = subprocess.run(comando, capture_output=True, text=True, check=False, timeout=30)
            if execucao.returncode != 0:
                raise ValueError("Não foi possível conferir o áudio do take.")
            texto = execucao.stderr
            inicios_silencio = [float(valor) for valor in re.findall(r"silence_start: ([0-9.]+)", texto)]
            fins_silencio = [float(valor) for valor in re.findall(r"silence_end: ([0-9.]+)", texto)]
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise ValueError("Conferência de áudio indisponível; abertura não gerada.") from exc
        copia = dict(trecho)
        if inicios_silencio and inicios_silencio[0] <= 0.03 and fins_silencio:
            proposto = inicio + fins_silencio[0] - 0.10
            seguro = float(trecho.get("safe_start", proposto))
            copia["start"] = max(inicio, min(proposto, seguro, inicio + 0.20))
        fim_silencio = next((marca for marca in reversed(inicios_silencio) if marca >= duracao - 0.5), None)
        if fim_silencio is not None and (not fins_silencio or fins_silencio[-1] >= duracao - .01):
            proposto = inicio + fim_silencio + 0.10
            seguro = float(trecho.get("safe_end", proposto))
            copia["end"] = min(fim, max(proposto, seguro, fim - 0.20))
        if float(copia["end"]) - float(copia["start"]) >= 3:
            copia["duration"] = round(float(copia["end"]) - float(copia["start"]), 2)
        else:
            copia = dict(trecho)
        resultado.append(copia)
    return resultado
