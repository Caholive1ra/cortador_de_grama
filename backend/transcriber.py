"""Transcrição local de mídia com faster-whisper."""

import logging
import os
import ssl
import traceback
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import httpx
import ctranslate2
from faster_whisper import WhisperModel
from huggingface_hub import set_client_factory
import truststore

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None
_model_runtime: tuple[str, str] | None = None

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto").strip().lower()


def _opcoes_de_runtime() -> list[tuple[str, str]]:
    """Prioriza CUDA, mas mantém CPU/int8 como fallback seguro."""
    if WHISPER_DEVICE == "cpu":
        return [("cpu", "int8")]
    if WHISPER_DEVICE == "cuda":
        return [("cuda", "float16")]
    try:
        tipos_cuda = ctranslate2.get_supported_compute_types("cuda")
        if "float16" in tipos_cuda:
            return [("cuda", "float16"), ("cpu", "int8")]
        if "int8_float16" in tipos_cuda:
            return [("cuda", "int8_float16"), ("cpu", "int8")]
    except Exception:
        logger.info("CUDA indisponível para Faster-Whisper; usando CPU/int8.")
    return [("cpu", "int8")]


class ModelUnavailableError(RuntimeError):
    """O modelo local ainda nao esta disponivel para a transcricao."""


def _configurar_certificados_windows() -> None:
    """Faz o Hugging Face respeitar certificados corporativos do Windows."""
    def criar_cliente() -> httpx.Client:
        contexto = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return httpx.Client(verify=contexto, follow_redirects=True, timeout=None)

    set_client_factory(criar_cliente)


def _carregar_modelo() -> WhisperModel:
    """Carrega o Whisper uma vez, priorizando GPU e caindo para CPU com segurança."""
    global _model, _model_runtime
    if _model is None:
        logger.info("Iniciando carregamento do WhisperModel (%s, device=%s).", WHISPER_MODEL, WHISPER_DEVICE)
        try:
            _configurar_certificados_windows()
            erros: list[str] = []
            for device, compute_type in _opcoes_de_runtime():
                try:
                    logger.info("Tentando WhisperModel (%s, %s, %s).", WHISPER_MODEL, device, compute_type)
                    _model = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)
                    _model_runtime = (device, compute_type)
                    break
                except Exception as erro_runtime:
                    erros.append(f"{device}/{compute_type}: {type(erro_runtime).__name__}: {erro_runtime}")
                    logger.warning("Whisper em %s/%s não iniciou; tentando alternativa.", device, compute_type)
            if _model is None:
                raise RuntimeError(" | ".join(erros) or "Nenhum runtime do Whisper foi iniciado.")
        except Exception as exc:
            logger.error("Nao foi possivel obter o modelo Whisper.\n%s", traceback.format_exc())
            raise ModelUnavailableError(
                "O modelo local de transcricao ainda nao esta instalado e nao foi possivel "
                "baixa-lo com os certificados atuais. Verifique a conexao corporativa e "
                "tente novamente."
            ) from exc
        logger.info("WhisperModel carregado em %s/%s.", *(_model_runtime or ("desconhecido", "desconhecido")))
    return _model


def _usar_cpu_com_seguranca(motivo: Exception) -> WhisperModel:
    """Troca o runtime para CPU se a GPU falhar somente durante a inferência.

    Em Windows, algumas instalações conseguem construir o modelo CUDA, mas só
    descobrem a ausência de cublas/cudnn ao codificar o primeiro áudio.
    """
    global _model, _model_runtime
    logger.warning(
        "CUDA não pôde transcrever (%s). Repetindo esta transcrição em CPU/int8.",
        motivo,
    )
    _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    _model_runtime = ("cpu", "int8")
    return _model


def _transcrever_com_modelo(modelo: WhisperModel, caminho: Path) -> list[dict]:
    segmentos: list[dict] = []
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
    return segmentos


def _erro_de_biblioteca_cuda(erro: Exception) -> bool:
    texto = str(erro).lower()
    return any(nome in texto for nome in ("cublas", "cudnn", "cuda", "cudart"))


def transcrever_audio(file_path: str) -> list[dict]:
    """Transcreve o áudio de um arquivo WAV ou MP4 e devolve segmentos.

    Returns:
        Lista de dicionários no formato ``{"start": float, "end": float, "text": str}``.
    """
    try:
        caminho = Path(file_path)
        if not caminho.is_file():
            raise FileNotFoundError(f"Arquivo de mídia não encontrado: {file_path}")

        modelo = _carregar_modelo()
        runtime = "/".join(_model_runtime or ("desconhecido", "desconhecido"))
        logger.info("Transcrição iniciada para o arquivo: %s | runtime=%s", file_path, runtime)

        try:
            segmentos = _transcrever_com_modelo(modelo, caminho)
        except RuntimeError as erro:
            if (_model_runtime or (None,))[0] != "cuda" or not _erro_de_biblioteca_cuda(erro):
                raise
            segmentos = _transcrever_com_modelo(_usar_cpu_com_seguranca(erro), caminho)

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
