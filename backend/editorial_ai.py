"""Revisao editorial semantica por um modelo Ollama local.

O modelo so pode decidir entre segmentos existentes. Isso impede que uma
resposta imprecisa invente timestamps ou altere a midia original.

O Ollama propoe cortes e o Gemini atua como aprovador final. O aprovador so
pode liberar indices que ja tenham sido propostos pelo revisor local.
"""

import json
import logging
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
import truststore

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_SEGMENTOS_POR_LOTE = 45
ULTIMO_DIAGNOSTICO: dict[str, object] = {"mode": "not_started"}
MOTIVOS_CORTE_PERMITIDOS = [
    "erro", "falsa_partida", "autocorrecao", "risada",
    "conversa_lateral", "devaneio", "problema_tecnico",
]


class EditorialModelError(RuntimeError):
    """O avaliador local nao pode ser usado de forma confiavel."""


class GeminiReviewError(RuntimeError):
    """O aprovador final Gemini nao pode ser usado de forma confiavel."""


def obter_diagnostico_editorial() -> dict[str, object]:
    """Retorna o resultado sanitizado da ultima revisao, sem credenciais."""
    return dict(ULTIMO_DIAGNOSTICO)


@dataclass(frozen=True)
class EditorialDecision:
    discard_indexes: set[int]
    reasons: dict[int, str]


def verificar_modelo_editorial() -> None:
    """Confirma que o servidor e o modelo local estao disponiveis."""
    try:
        resposta = _request("GET", "/api/tags")
    except (URLError, TimeoutError, HTTPError) as exc:
        raise EditorialModelError(
            "O revisor editorial local nao esta em execucao. Instale/inicie o Ollama "
            "e baixe o modelo qwen2.5:3b antes de processar a aula."
        ) from exc
    nomes = {item.get("name") for item in resposta.get("models", [])}
    if OLLAMA_MODEL not in nomes:
        raise EditorialModelError(
            f"O modelo editorial '{OLLAMA_MODEL}' nao esta instalado no Ollama."
        )


def decidir_cortes_semanticos(
    segmentos: list[dict], audio_path: str | None = None
) -> EditorialDecision:
    """Decide cortes com Gemini multimodal ou usa o fluxo local como fallback."""
    if GEMINI_API_KEY and audio_path:
        return _decidir_cortes_com_gemini(segmentos, audio_path)

    verificar_modelo_editorial()
    descartar: set[int] = set()
    motivos: dict[int, str] = {}
    for inicio in range(0, len(segmentos), MAX_SEGMENTOS_POR_LOTE):
        lote = segmentos[inicio:inicio + MAX_SEGMENTOS_POR_LOTE]
        decisao = _decidir_lote(lote)
        for indice, motivo in decisao.items():
            if 0 <= indice < len(lote):
                descartar.add(inicio + indice)
                motivos[inicio + indice] = motivo
    proposta = EditorialDecision(descartar, motivos)
    aprovacao = _aprovar_proposta_com_gemini(segmentos, proposta)
    logger.info(
        "Revisao editorial: Ollama propos %d corte(s); Gemini aprovou %d.",
        len(proposta.discard_indexes), len(aprovacao.discard_indexes),
    )
    return aprovacao


def _decidir_cortes_com_gemini(
    segmentos: list[dict], audio_path: str
) -> EditorialDecision:
    """Usa audio e transcricao para detectar eventos que texto nao revela."""
    transcricao = [
        {
            "i": indice,
            "inicio": round(float(item["start"]), 2),
            "fim": round(float(item["end"]), 2),
            "texto": str(item["text"]).strip(),
        }
        for indice, item in enumerate(segmentos)
    ]
    prompt = """Voce e o revisor principal de uma videoaula em portugues.
Analise o AUDIO e a transcricao com timestamps. Marque SOMENTE trechos que
devem sair da pre-edicao: erros declarados, falsas partidas, autocorrecoes
substituidas, risadas, conversas laterais, problemas tecnicos ou devaneios
claramente fora do contexto. Mantenha explicacoes, exemplos, repeticoes
didaticas e qualquer duvida. Use o audio para identificar risadas e conversas
que nao aparecem na transcricao.

REGRA DE RETAKE: quando o professor fizer uma tentativa, interromper a fala
para corrigir/recomecar/comentar que ficou ruim, e em seguida repetir a mesma
abertura ou explicacao, descarte TODOS os microtrechos da tentativa ANTERIOR
como "falsa_partida" e mantenha sempre a ULTIMA tentativa completa. Procure
esse padrao mesmo quando a frase de erro estiver entre as duas tentativas.
Nao aplique esta regra a repeticao didatica: se nao houver evidencia de
interrupcao, regravacao ou substituicao, mantenha as duas ocorrencias.
Responda APENAS JSON valido: {"discard":[{"i":0,"reason":"erro|falsa_partida|autocorrecao|risada|conversa_lateral|devaneio|problema_tecnico"}]}.
Use somente os indices existentes; nunca invente timestamps ou indices.
Transcricao:\n""" + json.dumps(transcricao, ensure_ascii=False)
    global ULTIMO_DIAGNOSTICO
    ULTIMO_DIAGNOSTICO = {"mode": "gemini_audio", "input_segments": len(segmentos)}
    try:
        resposta_texto = _gemini_audio_request(prompt, audio_path)
        bruto = json.loads(resposta_texto)
        motivos_permitidos = {
            "erro", "falsa_partida", "autocorrecao", "risada",
            "conversa_lateral", "devaneio", "problema_tecnico",
        }
        motivos: dict[int, str] = {}
        for item in bruto.get("discard", []):
            indice, motivo = int(item["i"]), str(item["reason"])
            if 0 <= indice < len(segmentos) and motivo in motivos_permitidos:
                motivos[indice] = motivo
        ULTIMO_DIAGNOSTICO.update({
            "status": "ok", "gemini_discarded": len(motivos),
            "gemini_response": resposta_texto,
        })
        logger.info("Gemini multimodal sugeriu %d corte(s).", len(motivos))
        return EditorialDecision(set(motivos), motivos)
    except (GeminiReviewError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        ULTIMO_DIAGNOSTICO.update({"status": "failed", "error": str(exc)})
        logger.error("Gemini multimodal falhou; nenhum corte semantico foi liberado: %s", exc)
        return EditorialDecision(set(), {})


def _gemini_audio_request(prompt: str, audio_path: str) -> str:
    """Envia WAV temporario pela Files API e o remove apos a analise."""
    if not GEMINI_API_KEY:
        raise GeminiReviewError("GEMINI_API_KEY ausente.")
    try:
        from google import genai
    except ImportError as exc:
        raise GeminiReviewError("Dependencia google-genai nao instalada.") from exc

    contexto = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options={
            "client_args": {"verify": contexto},
            "async_client_args": {"verify": contexto},
        },
    )
    arquivo = None
    try:
        arquivo = client.files.upload(
            file=audio_path, config={"mime_type": "audio/wav"}
        )
        resposta = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, arquivo],
            config={
                "temperature": 0,
                "response_mime_type": "application/json",
                "response_json_schema": {
                    "type": "object",
                    "properties": {
                        "discard": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "i": {"type": "integer"},
                                    "reason": {"type": "string", "enum": MOTIVOS_CORTE_PERMITIDOS},
                                },
                                "required": ["i", "reason"],
                            },
                        },
                    },
                    "required": ["discard"],
                },
                "max_output_tokens": 8192,
            },
        )
        if not resposta.text:
            raise GeminiReviewError("Gemini retornou resposta vazia.")
        return resposta.text
    except GeminiReviewError:
        raise
    except Exception as exc:
        raise GeminiReviewError(
            f"Gemini nao concluiu a revisao multimodal ({type(exc).__name__}: {exc})."
        ) from exc
    finally:
        if arquivo is not None:
            try:
                client.files.delete(name=arquivo.name)
            except Exception:
                logger.warning("Nao foi possivel remover o audio temporario do Gemini.")


def _aprovar_proposta_com_gemini(
    segmentos: list[dict], proposta: EditorialDecision
) -> EditorialDecision:
    """Faz fail-closed: sem Gemini valido, nenhum corte semantico e liberado."""
    if not proposta.discard_indexes:
        return proposta
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY ausente; cortes semanticos do Ollama nao foram liberados.")
        return EditorialDecision(set(), {})

    transcricao = [
        {
            "i": indice,
            "inicio": round(float(item["start"]), 2),
            "fim": round(float(item["end"]), 2),
            "texto": str(item["text"]).strip(),
        }
        for indice, item in enumerate(segmentos)
    ]
    candidatos = [
        {"i": indice, "reason": proposta.reasons[indice]}
        for indice in sorted(proposta.discard_indexes)
    ]
    prompt = """Voce e o aprovador final de cortes de uma videoaula em portugues.
O revisor local ja sugeriu os candidatos abaixo. Aprove SOMENTE um candidato se
o contexto completo provar que ele e erro declarado, falsa partida, autocorrecao
substituida, risada, conversa lateral, devaneio ou problema tecnico. Na duvida,
NAO aprove. Nunca adicione indices que nao estejam em candidatos. Repeticoes
didaticas, exemplos, transicoes e explicacoes devem ficar.
Responda APENAS JSON valido: {"approve":[{"i":0,"reason":"erro|falsa_partida|autocorrecao|risada|conversa_lateral|devaneio|problema_tecnico"}]}.
Candidatos do Ollama:\n""" + json.dumps(candidatos, ensure_ascii=False) + "\nTranscricao completa:\n" + json.dumps(transcricao, ensure_ascii=False)
    try:
        resposta = _gemini_request(prompt)
        bruto = json.loads(resposta)
        aprovados: set[int] = set()
        motivos: dict[int, str] = {}
        motivos_permitidos = {
            "erro", "falsa_partida", "autocorrecao", "risada",
            "conversa_lateral", "devaneio", "problema_tecnico",
        }
        for item in bruto.get("approve", []):
            indice, motivo = int(item["i"]), str(item["reason"])
            if indice in proposta.discard_indexes and motivo in motivos_permitidos:
                aprovados.add(indice)
                motivos[indice] = motivo
        return EditorialDecision(aprovados, motivos)
    except (GeminiReviewError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Gemini nao aprovou cortes; proposta do Ollama foi bloqueada: %s", exc)
        return EditorialDecision(set(), {})


def _gemini_request(prompt: str) -> str:
    """Solicita uma unica aprovacao JSON ao Gemini, sem registrar credenciais."""
    if not GEMINI_API_KEY:
        raise GeminiReviewError("GEMINI_API_KEY ausente.")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "maxOutputTokens": 2048,
        },
    }
    try:
        resposta = _request_url(
            "POST",
            f"{GEMINI_URL}/{GEMINI_MODEL}:generateContent",
            payload,
            {"x-goog-api-key": GEMINI_API_KEY},
            timeout=120,
        )
        return str(resposta["candidates"][0]["content"]["parts"][0]["text"])
    except (URLError, TimeoutError, HTTPError, KeyError, IndexError, TypeError) as exc:
        raise GeminiReviewError("Gemini nao retornou uma aprovacao valida.") from exc


def _decidir_lote(lote: list[dict]) -> dict[int, str]:
    transcricao = [
        {"i": indice, "inicio": round(float(item["start"]), 2), "fim": round(float(item["end"]), 2), "texto": str(item["text"]).strip()}
        for indice, item in enumerate(lote)
    ]
    prompt = """Voce e o revisor de uma videoaula em portugues. Decida quais trechos devem ser removidos na pre-edicao.
Remova SOMENTE: erros declarados pelo professor, falsas partidas que ele regrava, autocorrecoes cuja versao correta vem logo depois, risadas/conversas laterais, problemas tecnicos e devaneios claramente desconectados do assunto da aula.
MANTENHA: explicacoes, exemplos, repeticoes didaticas, perguntas retoricas, pausas curtas, transicoes e qualquer trecho em que haja duvida razoavel.
Use o contexto entre trechos. Nao resuma, nao invente texto, nao decida pela qualidade academica da explicacao.
Responda APENAS JSON valido no formato {"discard":[{"i":0,"reason":"erro|falsa_partida|autocorrecao|risada|conversa_lateral|devaneio|problema_tecnico"}]}.
Transcricao:\n""" + json.dumps(transcricao, ensure_ascii=False)
    resposta = _request("POST", "/api/generate", {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_ctx": 8192},
    })
    try:
        bruto = json.loads(resposta["response"])
        saida = {}
        for item in bruto.get("discard", []):
            indice, motivo = int(item["i"]), str(item["reason"])
            if motivo in {"erro", "falsa_partida", "autocorrecao", "risada", "conversa_lateral", "devaneio", "problema_tecnico"}:
                saida[indice] = motivo
        return saida
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EditorialModelError("O revisor editorial devolveu uma resposta invalida; nenhum corte foi aplicado.") from exc


def _request(method: str, endpoint: str, payload: dict | None = None) -> dict:
    return _request_url(method, f"{OLLAMA_URL}{endpoint}", payload, timeout=180)


def _request_url(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 180,
) -> dict:
    dados = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=dados, method=method)
    if dados is not None:
        request.add_header("Content-Type", "application/json")
    for nome, valor in (headers or {}).items():
        request.add_header(nome, valor)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
