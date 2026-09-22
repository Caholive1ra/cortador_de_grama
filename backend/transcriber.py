"""Transcrição local de mídia com faster-whisper."""

import logging
import os
import ssl
import traceback
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import httpx
from faster_whisper import WhisperModel
from huggingface_hub import set_client_factory
import truststore

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None


class ModelUnavailableError(RuntimeError):
    """O modelo local ainda nao esta disponivel para a transcricao."""


def _configurar_certificados_windows() -> None:
    """Faz o Hugging Face respeitar certificados corporativos do Windows."""
    def criar_cliente() -> httpx.Client:
        contexto = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return httpx.Client(verify=contexto, follow_redirects=True, timeout=None)

    set_client_factory(criar_cliente)


def _carregar_modelo() -> WhisperModel:
    """Carrega o WhisperModel (base / CPU / int8) uma única vez."""
    global _model
    if _model is None:
        logger.info("Iniciando carregamento do WhisperModel (base, cpu, int8).")
        try:
            _configurar_certificados_windows()
            _model = WhisperModel("base", device="cpu", compute_type="int8")
        except Exception as exc:
            logger.error("Nao foi possivel obter o modelo Whisper.\n%s", traceback.format_exc())
            raise ModelUnavailableError(
                "O modelo local de transcricao ainda nao esta instalado e nao foi possivel "
                "baixa-lo com os certificados atuais. Verifique a conexao corporativa e "
                "tente novamente."
            ) from exc
        logger.info("WhisperModel carregado.")
    return _model


def transcrever_audio(file_path: str) -> list[dict]:
    """Transcreve o áudio de um arquivo WAV ou MP4 e devolve segmentos.

    Returns:
        Lista de dicionários no formato ``{"start": float, "end": float, "text": str}``.
    """
    segmentos: list[dict] = []

    try:
        caminho = Path(file_path)
        if not caminho.is_file():
            raise FileNotFoundError(f"Arquivo de mídia não encontrado: {file_path}")

        modelo = _carregar_modelo()
        logger.info("Transcrição iniciada para o arquivo: %s", file_path)

        segmentos_whisper, _info = modelo.transcribe(
            str(caminho),
            language="pt",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            word_timestamps=True,
        )
        for segmento in segmentos_whisper:
            segmentos.append(
                {
                    "start": float(segmento.start),
                    "end": float(segmento.end),
                    "text": str(segmento.text),
                    "words": [
                        {
                            "start": float(palavra.start),
                            "end": float(palavra.end),
                            "word": str(palavra.word),
                        }
                        for palavra in (segmento.words or [])
                    ],
                }
            )

        logger.info(
            "Transcrição finalizada. Segmentos gerados: %s",
            len(segmentos),
        )
        return segmentos
    except FileNotFoundError:
        logger.critical(
            "Arquivo de mídia não encontrado: %s\n%s",
            file_path,
            traceback.format_exc(),
        )
        raise
    except ModelUnavailableError:
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
