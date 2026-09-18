"""Transcrição local de áudio com faster-whisper."""

import logging
import traceback
from pathlib import Path

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None


def _carregar_modelo() -> WhisperModel:
    """Carrega o WhisperModel (base / CPU / int8) uma única vez."""
    global _model
    if _model is None:
        logger.info("Iniciando carregamento do WhisperModel (base, cpu, int8).")
        _model = WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("WhisperModel carregado.")
    return _model


def transcrever_audio(file_path: str) -> list[dict]:
    """Transcreve um arquivo de áudio e devolve segmentos com timestamps.

    Returns:
        Lista de dicionários no formato ``{"start": float, "end": float, "text": str}``.
    """
    segmentos: list[dict] = []

    try:
        caminho = Path(file_path)
        if not caminho.is_file():
            raise FileNotFoundError(f"Arquivo de áudio não encontrado: {file_path}")

        modelo = _carregar_modelo()
        logger.info("Transcrição iniciada para o arquivo: %s", file_path)

        segmentos_whisper, _info = modelo.transcribe(str(caminho))
        for segmento in segmentos_whisper:
            segmentos.append(
                {
                    "start": float(segmento.start),
                    "end": float(segmento.end),
                    "text": str(segmento.text),
                }
            )

        logger.info(
            "Transcrição finalizada. Segmentos gerados: %s",
            len(segmentos),
        )
        return segmentos
    except FileNotFoundError:
        logger.critical(
            "Arquivo de áudio não encontrado: %s\n%s",
            file_path,
            traceback.format_exc(),
        )
        raise
    except MemoryError:
        logger.critical(
            "Falha de memória ao transcrever o arquivo: %s\n%s",
            file_path,
            traceback.format_exc(),
        )
        raise
    except Exception:
        logger.critical(
            "Falha ao transcrever o arquivo: %s\n%s",
            file_path,
            traceback.format_exc(),
        )
        raise
