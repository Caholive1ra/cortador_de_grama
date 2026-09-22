"""Extração de metadados e áudio com FFmpeg/ffprobe."""

import json
import logging
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


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
    resultado = subprocess.run(comando, capture_output=True, text=True, check=True)
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
    resultado = subprocess.run(comando, capture_output=True, text=True, check=True)
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
            subprocess.run(comando, capture_output=True, text=True, check=True)
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
