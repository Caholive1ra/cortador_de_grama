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
        "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
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
    duracao = float(dados["format"]["duration"])
    if fps <= 0 or duracao <= 0:
        raise ValueError(f"Metadados inválidos para: {file_path}")
    return {
        "path": str(caminho.resolve()),
        "duration": duracao,
        "fps": fps,
        "width": int(stream["width"]),
        "height": int(stream["height"]),
    }


@contextmanager
def extrair_audio_temporario(file_path: str) -> Iterator[str]:
    """Extrai WAV mono/16 kHz e remove o arquivo ao final do processamento."""
    descritor, audio_path = tempfile.mkstemp(prefix="decupagem_", suffix=".wav")
    os.close(descritor)
    try:
        comando = [
            "ffmpeg", "-y", "-v", "error", "-i", file_path,
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", audio_path,
        ]
        logger.info("Extraindo áudio temporário do PGM.")
        subprocess.run(comando, capture_output=True, text=True, check=True)
        yield audio_path
    finally:
        try:
            os.unlink(audio_path)
        except FileNotFoundError:
            pass
