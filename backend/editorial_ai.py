"""Revisao editorial semantica por um modelo Ollama local.

O modelo so pode decidir entre segmentos existentes. Isso impede que uma
resposta imprecisa invente timestamps ou altere a midia original.

O Ollama propoe cortes e o Gemini atua como aprovador final. O aprovador so
pode liberar indices que ja tenham sido propostos pelo revisor local.
"""

import json
import logging
import os
import re
import ssl
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
import truststore

# O SDK Gemini usa clientes HTTP internos. A injecao global garante que todos
# eles respeitem o repositório de certificados do Windows, inclusive em redes
# corporativas que interceptam HTTPS.
truststore.inject_into_ssl()
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)
T = TypeVar("T")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
NVIDIA_API_KEY = (os.getenv("NVIDIA_API_KEY") or "").strip()
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
# Fallback textual Kimi hospedado pela NVIDIA; nao reutiliza a chave Groq.
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "moonshotai/kimi-k3")
NVIDIA_REASONING_EFFORT = os.getenv("NVIDIA_REASONING_EFFORT", "max")
# gemini-2.5-flash foi aposentado para novas contas. Comecamos por modelos
# atuais, deixando uma variante Lite como rota de menor demanda.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODELS = tuple(
    modelo.strip()
    for modelo in os.getenv(
        "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.6-flash"
    ).split(",")
    if modelo.strip()
)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_SEGMENTOS_POR_LOTE = 45
MAX_TENTATIVAS_JSON_POR_MODELO = 2
MAX_CANDIDATOS_ECONOMICOS = 72
TAMANHO_LOTE_ECONOMICO = 30
CONTEXTO_ECONOMICO = 2
ULTIMO_DIAGNOSTICO: dict[str, object] = {"mode": "not_started"}
ULTIMO_MODELO_GEMINI_USADO: str | None = None
ULTIMO_MODELO_GEMINI_TEXTUAL_USADO: str | None = None
ULTIMO_MODELO_NVIDIA_USADO: str | None = None
MOTIVOS_CORTE_PERMITIDOS = [
    "erro", "falsa_partida", "autocorrecao", "risada",
    "conversa_lateral", "devaneio", "problema_tecnico", "comentario_bastidor",
]


class EditorialModelError(RuntimeError):
    """O avaliador local nao pode ser usado de forma confiavel."""


class GeminiReviewError(RuntimeError):
    """O aprovador final Gemini nao pode ser usado de forma confiavel."""


class NvidiaReviewError(RuntimeError):
    """O revisor textual NVIDIA nao pode ser usado de forma confiavel."""


class LayaReviewError(RuntimeError):
    """O classificador local Laya nao pode ser usado de forma confiavel."""


def obter_diagnostico_editorial() -> dict[str, object]:
    """Retorna o resultado sanitizado da ultima revisao, sem credenciais."""
    return dict(ULTIMO_DIAGNOSTICO)


def obter_ultimo_modelo_gemini_textual() -> str | None:
    """Modelo usado na ultima chamada textual, inclusive lettering."""
    return ULTIMO_MODELO_GEMINI_TEXTUAL_USADO


def obter_ultimo_modelo_textual() -> str | None:
    """Ultimo modelo remoto usado para uma tarefa textual, sem expor chaves."""
    return ULTIMO_MODELO_GEMINI_TEXTUAL_USADO or ULTIMO_MODELO_NVIDIA_USADO


@dataclass(frozen=True)
class EditorialDecision:
    discard_indexes: set[int]
    reasons: dict[int, str]
    review_indexes: set[int] = field(default_factory=set)
    review_reasons: dict[int, str] = field(default_factory=dict)


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
    segmentos: list[dict], audio_path: str | None = None, modo_economico: bool = False,
) -> EditorialDecision:
    """Decide cortes com Gemini multimodal ou usa o fluxo local como fallback."""
    global ULTIMO_DIAGNOSTICO
    if modo_economico:
        for provedor in ("gemini", "nvidia"):
            if _provedor_configurado(provedor):
                decisao = _decidir_cortes_economicos(segmentos, provedor)
                if ULTIMO_DIAGNOSTICO.get("status") == "ok":
                    return _aplicar_retakes_explicitos(segmentos, decisao)
    if not modo_economico and GEMINI_API_KEY and audio_path:
        decisao = _decidir_cortes_com_gemini(segmentos, audio_path)
        if ULTIMO_DIAGNOSTICO.get("status") == "ok":
            return _aplicar_retakes_explicitos(segmentos, decisao)
    # Sem Gemini, a NVIDIA recebe a mesma transcricao completa e as mesmas
    # regras editoriais. Ela nao recebe o WAV, portanto nao pode inferir sons
    # que nao estejam transcritos, mas nao fica limitada a poucos candidatos.
    if NVIDIA_API_KEY and not modo_economico:
        logger.info("Gemini nao concluiu a revisao; iniciando fallback NVIDIA textual completo.")
        decisao = _decidir_cortes_textuais_completos(segmentos, "nvidia")
        if ULTIMO_DIAGNOSTICO.get("status") == "ok":
            return _aplicar_retakes_explicitos(segmentos, decisao)
    try:
        logger.info("Provedores remotos nao concluiram a revisao; iniciando fallback Laya local.")
        decisao = _decidir_cortes_com_laya(segmentos)
        if ULTIMO_DIAGNOSTICO.get("status") == "ok":
            return _aplicar_retakes_explicitos(segmentos, decisao)
    except LayaReviewError as exc:
        logger.warning("Laya indisponivel; seguindo para Ollama: %s", exc)

    ULTIMO_DIAGNOSTICO = {"mode": "ollama", "ollama_model": OLLAMA_MODEL}
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
    return _aplicar_retakes_explicitos(segmentos, aprovacao)


def _normalizar_para_candidato(texto: str) -> str:
    sem_acentos = "".join(
        caractere for caractere in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(caractere) != "Mn"
    )
    return " ".join(re.findall(r"[a-z0-9]+", sem_acentos))


def _candidatos_economicos(segmentos: list[dict]) -> list[int]:
    """Seleciona somente trechos com sinais locais de erro ou retake."""
    pistas = (
        "errei", "desculpa", "corta", "cortar", "recomecar", "novamente",
        "nao ficou bom", "falei errado", "gancho", "roteiro", "gravacao",
        "edicao", "deixa eu ver", "voltar essa parte", "risada",
    )
    candidatos: set[int] = set()
    normalizados = [_normalizar_para_candidato(str(item.get("text", ""))) for item in segmentos]
    for indice, texto in enumerate(normalizados):
        if any(pista in texto for pista in pistas):
            candidatos.add(indice)
        if indice and len(texto.split()) >= 3 and len(normalizados[indice - 1].split()) >= 3:
            similaridade = SequenceMatcher(None, normalizados[indice - 1], texto).ratio()
            ha_indicio_local = any(
                any(pista in normalizados[posicao] for pista in pistas)
                for posicao in range(max(0, indice - 2), indice + 1)
            )
            if ha_indicio_local and (
                similaridade >= 0.78 or texto.startswith(normalizados[indice - 1])
            ):
                candidatos.update({indice - 1, indice})
    # O começo abandonado pode estar varios segmentos antes da frase "vou
    # recomecar"; inclua ambos os lados para que Gemini/NVIDIA/Laya vejam o fato.
    for indice in _detectar_retakes_explicitos(segmentos):
        candidatos.add(indice)
    return sorted(candidatos)[:MAX_CANDIDATOS_ECONOMICOS]


def _detectar_retakes_explicitos(segmentos: list[dict]) -> dict[int, str]:
    """Confirma retakes por repeticao de abertura + aviso explicito de reinicio.

    A regra exige os dois sinais. Isso evita confundir uma repeticao didatica
    comum com uma tomada descartada, mas impede que qualquer IA mantenha por
    engano uma abertura que o proprio professor abandonou.
    """
    pistas_reinicio = (
        "vou recomecar", "vamos recomecar", "comecar de novo", "comeco de novo",
        "vamos comecar de novo", "deixa eu recomecar", "voltar do inicio",
        "vou voltar", "falei errado", "nao ficou bom", "corta ai",
    )
    textos = [_normalizar_para_candidato(str(item.get("text", ""))) for item in segmentos]
    descartes: dict[int, str] = {}
    for marcador, texto_marcador in enumerate(textos):
        if not any(pista in texto_marcador for pista in pistas_reinicio):
            continue
        # Procura a mesma abertura antes e depois do aviso. Limites curtos
        # reduzem falsos positivos em aulas longas que retomam o tema depois.
        for anterior in range(max(0, marcador - 12), marcador):
            palavras_anteriores = textos[anterior].split()
            if len(palavras_anteriores) < 4:
                continue
            for posterior in range(marcador + 1, min(len(textos), marcador + 13)):
                palavras_posteriores = textos[posterior].split()
                if len(palavras_posteriores) < 4:
                    continue
                comuns = sum(
                    a == b for a, b in zip(palavras_anteriores[:8], palavras_posteriores[:8])
                )
                similaridade = SequenceMatcher(None, textos[anterior], textos[posterior]).ratio()
                if comuns >= 4 or similaridade >= 0.88:
                    descartes[anterior] = "falsa_partida"
    # Caso muito comum de gravação: o professor repete a abertura inteira sem
    # verbalizar o erro. Só vale no começo e com DOIS trechos consecutivos
    # praticamente idênticos, um sinal bem mais forte que uma frase didática.
    limite_inicial = min(len(textos) - 1, 24)
    for anterior in range(limite_inicial):
        palavras_anteriores = textos[anterior].split()
        if len(palavras_anteriores) < 4:
            continue
        for posterior in range(anterior + 1, limite_inicial):
            try:
                dentro_do_inicio = float(segmentos[posterior].get("start", 0)) <= 90.0
            except (TypeError, ValueError):
                dentro_do_inicio = False
            if not dentro_do_inicio or posterior + 1 >= len(textos):
                continue
            primeira = SequenceMatcher(None, textos[anterior], textos[posterior]).ratio()
            segunda = SequenceMatcher(None, textos[anterior + 1], textos[posterior + 1]).ratio()
            if primeira >= 0.92 and segunda >= 0.92:
                descartes[anterior] = "falsa_partida"
                descartes[anterior + 1] = "falsa_partida"
    return descartes


def _aplicar_retakes_explicitos(
    segmentos: list[dict], decisao: EditorialDecision,
) -> EditorialDecision:
    """Une uma decisão de IA aos retakes comprovados por regra determinística."""
    from retake_review import revisar
    adicionais, pendentes, registros = revisar(segmentos, _solicitar_revisao_retake)
    global ULTIMO_DIAGNOSTICO
    ULTIMO_DIAGNOSTICO['retake_review'] = registros
    motivos_base = {**decisao.reasons, **adicionais}
    # A tomada substituta aprovada precisa permanecer inteira.
    for registro in registros:
        if 'replacement' in registro:
            for i in range(registro['replacement'][0], registro['replacement'][1] + 1):
                motivos_base.pop(i, None)
    revisoes_base = {**decisao.review_reasons, **pendentes}
    revisoes_base = {i: r for i, r in revisoes_base.items() if i not in motivos_base}
    decisao = EditorialDecision(set(motivos_base), motivos_base, set(revisoes_base), revisoes_base)
    if registros:
        # O resultado contextual tem precedencia sobre similaridade de palavras.
        return decisao
    confirmados = _detectar_retakes_explicitos(segmentos)
    novos = {indice: motivo for indice, motivo in confirmados.items() if indice not in decisao.discard_indexes}
    if not novos:
        return decisao
    motivos = {**decisao.reasons, **novos}
    revisoes = {
        indice: motivo for indice, motivo in decisao.review_reasons.items()
        if indice not in novos
    }
    ULTIMO_DIAGNOSTICO["explicit_retakes_discarded"] = sorted(novos)
    logger.info("Regra local confirmou %d primeira(s) tomada(s) repetida(s).", len(novos))
    return EditorialDecision(
        set(motivos), motivos, set(revisoes), revisoes,
    )


def _contexto_economico(segmentos: list[dict], indice: int) -> dict:
    inicio = max(0, indice - CONTEXTO_ECONOMICO)
    fim = min(len(segmentos), indice + CONTEXTO_ECONOMICO + 1)
    return {
        "candidate_id": indice,
        "context": [
            {"i": pos, "texto": str(segmentos[pos].get("text", "")).strip()}
            for pos in range(inicio, fim)
        ],
    }


def _decidir_cortes_economicos(segmentos: list[dict], provedor: str = "gemini") -> EditorialDecision:
    """Revisa poucos candidatos textuais, sem subir o audio completo."""
    candidatos = _candidatos_economicos(segmentos)
    global ULTIMO_DIAGNOSTICO
    ULTIMO_DIAGNOSTICO = {
        "mode": f"{provedor}_economic", "input_segments": len(segmentos),
        "candidate_count": len(candidatos), "batches": [],
        "provider": provedor,
    }
    motivos: dict[int, str] = {}
    revisoes: dict[int, str] = {}
    permitidos = set(MOTIVOS_CORTE_PERMITIDOS)
    for numero, inicio in enumerate(range(0, len(candidatos), TAMANHO_LOTE_ECONOMICO), start=1):
        ids = candidatos[inicio:inicio + TAMANHO_LOTE_ECONOMICO]
        prompt = """Voce revisa candidatos de corte de uma videoaula. Decida SOMENTE os candidate_id recebidos.
Descarte apenas erro declarado, falsa partida substituida, autocorrecao, conversa de bastidor ou risada.
RETAKE PRIORITARIO: se uma abertura/frase se repetir antes e depois de "vou recomecar",
"comecar de novo" ou "falei errado", descarte a ocorrencia anterior como
"falsa_partida" e mantenha a ultima; isso nao e repeticao didatica.
Tambem trate duas sequencias consecutivas quase identicas no inicio da aula como retake.
Na duvida, mantenha ou marque review. Nunca invente fatos, tempos ou IDs.
Responda JSON: {"discard":[{"i":0,"reason":"erro|falsa_partida|autocorrecao|risada|conversa_lateral|devaneio|problema_tecnico|comentario_bastidor"}],"review":[{"i":0,"reason":"duvida_editorial"}]}.
Candidatos com contexto local:\n""" + json.dumps(
            [_contexto_economico(segmentos, indice) for indice in ids], ensure_ascii=False
        )
        registro = {"batch": numero, "candidate_ids": ids}
        try:
            bruto = json.loads(solicitar_json_textual(prompt, provedor))
            _validar_indices_resposta(bruto, set(ids))
            for item in bruto.get("discard", []):
                indice, motivo = int(item["i"]), str(item["reason"])
                if indice in ids and motivo in permitidos:
                    motivos[indice] = motivo
            for item in bruto.get("review", []):
                indice = int(item["i"])
                if indice in ids and indice not in motivos:
                    revisoes[indice] = "duvida_editorial"
            registro.update({"status": "ok", "model": _modelo_textual_atual(provedor)})
        except (GeminiReviewError, NvidiaReviewError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            registro.update({"status": "failed", "error": str(exc)[:500]})
            logger.warning("Lote economico %d falhou; nenhum corte dele foi aplicado: %s", numero, exc)
        ULTIMO_DIAGNOSTICO["batches"].append(registro)
    sucesso = any(item["status"] == "ok" for item in ULTIMO_DIAGNOSTICO["batches"])
    ULTIMO_DIAGNOSTICO.update({
        "status": "ok" if sucesso else "failed", "discarded": len(motivos), "review": len(revisoes),
    })
    return EditorialDecision(set(motivos), motivos, set(revisoes), revisoes)


def _decidir_cortes_com_laya(segmentos: list[dict]) -> EditorialDecision:
    """Usa Laya apenas para validar candidatos locais; nunca cria timestamps/texto."""
    candidatos = _candidatos_economicos(segmentos)
    global ULTIMO_DIAGNOSTICO
    ULTIMO_DIAGNOSTICO = {
        "mode": "laya_local", "provider": "laya", "input_segments": len(segmentos),
        "candidate_count": len(candidatos), "batches": [],
    }
    if not candidatos:
        ULTIMO_DIAGNOSTICO.update({"status": "ok", "discarded": 0, "review": 0})
        return EditorialDecision(set(), {})
    try:
        from laya import Router
        router = Router()
    except Exception as exc:
        raise LayaReviewError(f"Laya nao pode iniciar ({type(exc).__name__}: {exc})") from exc

    motivos: dict[int, str] = {}
    retakes_confirmados = _detectar_retakes_explicitos(segmentos)
    for indice in candidatos:
        pergunta = {
            "cut": {
                "type": "choice",
                "instructions": "Classifique de forma conservadora um candidato de corte em uma videoaula em portugues. Se a mesma abertura aparece antes e depois de uma fala de reinicio/correcao, ou se duas sequencias consecutivas quase identicas ocorrerem no inicio, a ocorrencia anterior e falsa partida e deve ser descartada.",
                "criteria": {
                    "discard": "O contexto mostra claramente erro, retake, bastidor, risada ou conversa lateral.",
                    "keep": "E conteudo didatico, ou nao ha evidencia suficiente para cortar.",
                },
            }
        }
        try:
            resultado = router.predict(json.dumps(_contexto_economico(segmentos, indice), ensure_ascii=False), pergunta)
            escolha = str(resultado.get("answers", {}).get("cut", {}).get("choice", "keep")).lower()
            ULTIMO_DIAGNOSTICO["batches"].append({"candidate_id": indice, "status": "ok", "choice": escolha})
            if escolha == "discard":
                motivos[indice] = retakes_confirmados.get(indice, "comentario_bastidor")
        except Exception as exc:
            ULTIMO_DIAGNOSTICO["batches"].append({"candidate_id": indice, "status": "failed", "error": str(exc)[:500]})
    sucesso = any(item["status"] == "ok" for item in ULTIMO_DIAGNOSTICO["batches"])
    ULTIMO_DIAGNOSTICO.update({"status": "ok" if sucesso else "failed", "discarded": len(motivos), "review": 0})
    return EditorialDecision(set(motivos), motivos)


def _transcricao_editorial(segmentos: list[dict]) -> list[dict]:
    return [
        {
            "i": indice,
            "inicio": round(float(item["start"]), 2),
            "fim": round(float(item["end"]), 2),
            "texto": str(item["text"]).strip(),
        }
        for indice, item in enumerate(segmentos)
    ]


def _prompt_revisao_editorial(transcricao: list[dict], incluir_audio: bool) -> str:
    """Contrato editorial unico para Gemini e NVIDIA.

    A diferenca entre provedores e somente a modalidade disponivel. As regras,
    motivos aceitos, indices validos e a postura conservadora sao identicos.
    """
    contexto_audio = (
        "Use o audio para identificar risadas e conversas que nao aparecem na transcricao."
        if incluir_audio else
        "O audio nao esta disponivel nesta revisao; marque risadas ou conversas somente quando houver evidencia na transcricao."
    )
    material_disponivel = "o AUDIO e a transcricao com timestamps" if incluir_audio else "a transcricao com timestamps"
    return """Voce e o revisor principal de uma videoaula em portugues.
Analise """ + material_disponivel + """. Marque SOMENTE trechos que
devem sair da pre-edicao: erros declarados, falsas partidas, autocorrecoes
substituidas, risadas, conversas laterais, problemas tecnicos ou devaneios
claramente fora do contexto. Mantenha explicacoes, exemplos, repeticoes
didaticas e qualquer duvida. """ + contexto_audio + """

FALAS DE BASTIDOR DEVEM SER DESCARTADAS, nao marcadas para revisao: comentarios
para si/equipe sobre encontrar um gancho, roteiro, edicao, gravacao, proxima
tomada, reiniciar, checar equipamento ou organizar a fala. Por exemplo,
"deixa eu ver um gancho aqui" nao pertence a aula e deve ser descartado como
"comentario_bastidor". Se uma fala nao ensina nada e parece uma anotacao de
producao, descarte-a; use "review" somente quando houver duvida real se ela
faz parte da explicacao pedagogica.

REGRA DE RETAKE: quando o professor fizer uma tentativa, interromper a fala
para corrigir/recomecar/comentar que ficou ruim, e em seguida repetir a mesma
abertura ou explicacao, descarte TODOS os microtrechos da tentativa ANTERIOR
como "falsa_partida" e mantenha sempre a ULTIMA tentativa completa. Procure
esse padrao mesmo quando a frase de erro estiver entre as duas tentativas.
Esta regra tem prioridade: se o inicio da aula/frase for repetido apos "vou
recomecar", "comecar de novo", "falei errado" ou equivalente, marque a
PRIMEIRA ocorrencia como "falsa_partida", ainda que ela pareca valida isoladamente.
Se duas sequencias consecutivas quase identicas ocorrerem no inicio da aula, descarte a primeira mesmo sem fala de correcao.
Nao aplique esta regra a repeticao didatica: se nao houver evidencia de
interrupcao, regravacao ou substituicao, mantenha as duas ocorrencias.
Se estiver em duvida razoavel entre manter ou descartar um trecho, NAO o
descarte: coloque-o em "review" para o editor revisar no Premiere.
Responda APENAS JSON valido: {"discard":[{"i":0,"reason":"erro|falsa_partida|autocorrecao|risada|conversa_lateral|devaneio|problema_tecnico|comentario_bastidor"}],"review":[{"i":0,"reason":"duvida_editorial"}]}.
Use somente os indices existentes; nunca invente timestamps ou indices.
Transcricao:\n""" + json.dumps(transcricao, ensure_ascii=False)


def _decisao_de_resposta_editorial(
    resposta_texto: str, segmentos: list[dict], provedor: str,
) -> EditorialDecision:
    bruto = json.loads(resposta_texto)
    _validar_indices_resposta(bruto, set(range(len(segmentos))))
    motivos: dict[int, str] = {}
    revisoes: dict[int, str] = {}
    for item in bruto.get("discard", []):
        indice, motivo = int(item["i"]), str(item["reason"])
        if indice in range(len(segmentos)) and motivo in MOTIVOS_CORTE_PERMITIDOS:
            motivos[indice] = motivo
    for item in bruto.get("review", []):
        indice = int(item["i"])
        if indice in range(len(segmentos)) and indice not in motivos:
            revisoes[indice] = "duvida_editorial"
    return EditorialDecision(set(motivos), motivos, set(revisoes), revisoes)


def _decidir_cortes_textuais_completos(
    segmentos: list[dict], provedor: str,
) -> EditorialDecision:
    """Aplica o contrato completo quando o provedor so aceita texto."""
    global ULTIMO_DIAGNOSTICO
    prompt = _prompt_revisao_editorial(_transcricao_editorial(segmentos), incluir_audio=False)
    ULTIMO_DIAGNOSTICO = {
        "mode": f"{provedor}_text", "provider": provedor,
        "input_segments": len(segmentos), "audio_available": False,
    }
    try:
        decisao = _decisao_de_resposta_editorial(
            solicitar_json_textual(prompt, provedor), segmentos, provedor,
        )
        ULTIMO_DIAGNOSTICO.update({
            "status": "ok", "model": _modelo_textual_atual(provedor),
            "discarded": len(decisao.discard_indexes), "review": len(decisao.review_indexes),
        })
        return decisao
    except (GeminiReviewError, NvidiaReviewError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        ULTIMO_DIAGNOSTICO.update({"status": "failed", "error": str(exc)[:1000]})
        logger.error("%s nao concluiu a revisao textual completa: %s", provedor.capitalize(), exc)
        return EditorialDecision(set(), {})


def _decidir_cortes_com_gemini(
    segmentos: list[dict], audio_path: str
) -> EditorialDecision:
    """Usa audio e transcricao para detectar eventos que texto nao revela."""
    transcricao = _transcricao_editorial(segmentos)
    prompt = _prompt_revisao_editorial(transcricao, incluir_audio=True)
    global ULTIMO_DIAGNOSTICO
    ULTIMO_DIAGNOSTICO = {
        "mode": "gemini_audio", "input_segments": len(segmentos),
        "gemini_models_attempted": list(_modelos_gemini()),
    }
    try:
        resposta_texto = _gemini_audio_request(prompt, audio_path)
        decisao = _decisao_de_resposta_editorial(resposta_texto, segmentos, "gemini")
        ULTIMO_DIAGNOSTICO.update({
            "status": "ok", "gemini_discarded": len(decisao.discard_indexes),
            "gemini_review": len(decisao.review_indexes),
            "gemini_model_used": ULTIMO_MODELO_GEMINI_USADO or GEMINI_MODEL,
            "gemini_response": resposta_texto,
        })
        logger.info("Gemini multimodal sugeriu %d corte(s).", len(decisao.discard_indexes))
        return decisao
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
    global ULTIMO_MODELO_GEMINI_USADO
    arquivo = None
    try:
        arquivo = client.files.upload(
            file=audio_path, config={"mime_type": "audio/wav"}
        )
        erros: list[str] = []
        for modelo in _modelos_gemini():
            try:
                resposta = _com_retry_remoto(
                    lambda: _gerar_conteudo(client, prompt, arquivo, modelo), modelo,
                )
            except Exception as exc:
                if _erro_gemini_fatal(exc):
                    logger.error("Gemini %s falhou de forma nao recuperavel: %s", modelo, exc, exc_info=True)
                    raise
                erros.append(f"{modelo}: {type(exc).__name__}: {exc}")
                if _erro_gemini_quota_diaria(exc):
                    logger.warning("Gemini atingiu a cota diaria; liberando o fallback NVIDIA sem tentar outros modelos.")
                    break
                logger.warning("Gemini %s falhou; tentando o proximo modelo.", modelo)
                continue
            for tentativa in range(1, MAX_TENTATIVAS_JSON_POR_MODELO + 1):
                try:
                    texto = _extrair_json_da_resposta_gemini(resposta)
                    if 'Transcricao:\n' in prompt:
                        transcricao_enviada = json.loads(prompt.rsplit('Transcricao:\n', 1)[1])
                        _validar_indices_resposta(json.loads(texto), {item['i'] for item in transcricao_enviada})
                    ULTIMO_MODELO_GEMINI_USADO = modelo
                    logger.info("Gemini %s respondeu com JSON valido; fallback NVIDIA/Laya nao sera necessario.", modelo)
                    return texto
                except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
                    tipo = "JSON invalido" if isinstance(exc, json.JSONDecodeError) else "resposta rejeitada"
                    erros.append(f"{modelo} (tentativa {tentativa}): {tipo}: {exc}")
                    if tentativa == MAX_TENTATIVAS_JSON_POR_MODELO:
                        logger.warning("Gemini %s teve resposta rejeitada em %d tentativa(s); tentando o proximo modelo.", modelo, tentativa)
                        break
                    logger.warning("Gemini %s teve resposta rejeitada (%s); repetindo a solicitacao.", modelo, exc)
                    try:
                        resposta = _com_retry_remoto(
                            lambda: _gerar_conteudo(client, prompt, arquivo, modelo), modelo,
                        )
                    except Exception as retry_exc:
                        if _erro_gemini_fatal(retry_exc):
                            logger.error("Gemini %s falhou de forma nao recuperavel: %s", modelo, retry_exc, exc_info=True)
                            raise
                        erros.append(f"{modelo} (tentativa {tentativa + 1}): {type(retry_exc).__name__}: {retry_exc}")
                        if _erro_gemini_quota_diaria(retry_exc):
                            raise GeminiReviewError(
                                "Cota diaria do Gemini atingida; usando fallback configurado."
                            ) from retry_exc
                        break
        raise GeminiReviewError("Nenhum modelo Gemini concluiu a revisao: " + " | ".join(erros))
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


def _gerar_conteudo(client, prompt: str, arquivo, modelo: str):
    """Pede uma decisao JSON a um modelo Gemini especifico."""
    config = {
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
                "review": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"i": {"type": "integer"}, "reason": {"type": "string"}},
                        "required": ["i", "reason"],
                    },
                },
            },
            "required": ["discard", "review"],
        },
        "max_output_tokens": 8192,
        "temperature": 0,
        "automatic_function_calling": {"disable": True},
    }
    return client.models.generate_content(
        model=modelo, contents=[prompt, arquivo], config=config,
    )


def _extrair_json_da_resposta_gemini(resposta) -> str:
    """Retorna JSON valido, preferindo a estrutura parseada pelo SDK.

    Alguns modelos podem devolver ``response.text`` incompleto mesmo com schema.
    Quando o SDK ja conseguiu interpretar a resposta, ``parsed`` e mais confiavel
    que reconstruir JSON a partir desse texto.
    """
    parsed = getattr(resposta, "parsed", None)
    if parsed is not None:
        texto = parsed if isinstance(parsed, str) else json.dumps(parsed, ensure_ascii=False)
    else:
        texto = str(getattr(resposta, "text", "") or "").strip()
        if texto.startswith("```json") and texto.endswith("```"):
            texto = texto[7:-3].strip()
        elif texto.startswith("```") and texto.endswith("```"):
            texto = texto[3:-3].strip()
    if not texto:
        raise json.JSONDecodeError("resposta vazia", texto, 0)
    json.loads(texto)
    return texto


def _gemini_esta_sobrecarregado(erro: Exception) -> bool:
    texto = str(erro).upper()
    return "503" in texto and "UNAVAILABLE" in texto


def _detalhe_erro_remoto(erro: Exception) -> str:
    """Le o corpo HTTP uma vez e remove credenciais dos diagnosticos."""
    detalhe = getattr(erro, "_detalhe_remoto", None)
    if detalhe is not None:
        return detalhe
    detalhe = str(erro)
    if isinstance(erro, HTTPError):
        try:
            detalhe += " | " + erro.read().decode("utf-8", errors="replace")[:1000]
        except (OSError, ValueError):
            pass
    for chave in (GEMINI_API_KEY, NVIDIA_API_KEY):
        if chave:
            detalhe = detalhe.replace(chave, "[credencial removida]")
    erro._detalhe_remoto = detalhe
    return detalhe


def _com_retry_remoto(operacao: Callable[[], T], modelo: str) -> T:
    """Ate tres tentativas para falhas transitorias, nunca para chave invalida."""
    for tentativa in range(3):
        try:
            return operacao()
        except Exception as exc:
            codigo = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            detalhe = _detalhe_erro_remoto(exc)
            transitorio = codigo in (429, 500, 502, 503, 504) or isinstance(exc, (TimeoutError, URLError))
            if isinstance(exc, HTTPError):
                transitorio = codigo in (429, 500, 502, 503, 504)
            if codigo is None:
                transitorio = transitorio or bool(re.search(r"\b(429|500|502|503|504)\b", detalhe))
            if tentativa == 2 or not transitorio or _erro_gemini_fatal(exc):
                raise
            if _erro_gemini_quota_diaria(exc):
                logger.warning("IA %s atingiu uma cota de longa duracao; sem repeticao automatica.", modelo)
                raise
            espera = 2 ** (tentativa + 1)
            headers = getattr(exc, "headers", None)
            if headers:
                try:
                    espera = max(espera, min(30, float(headers.get("Retry-After", 0))))
                except (TypeError, ValueError):
                    pass
            logger.warning("IA %s temporariamente indisponivel; tentativa %d/3 em %ss.", modelo, tentativa + 2, espera)
            time.sleep(espera)


def _erro_gemini_recuperavel(erro: Exception) -> bool:
    texto = str(erro).upper()
    return any(indicador in texto for indicador in (
        "400", "404", "409", "422", "429", "500", "502", "503", "504",
        "UNAVAILABLE", "RESOURCE_EXHAUSTED", "TIMEOUT", "CONNECT", "INVALID_ARGUMENT",
        "NOT_FOUND", "FAILED_PRECONDITION",
    ))


def _erro_gemini_quota_diaria(erro: Exception) -> bool:
    """Distingue cota esgotada de um 429 curto, que ainda pode ser repetido."""
    texto = _detalhe_erro_remoto(erro).upper()
    return any(indicador in texto for indicador in (
        "GENERATEREQUESTSPERDAY", "PERDAYPERPROJECT", "QUOTA EXCEEDED",
        "FREE_TIER_REQUESTS", "RETRY IN ",
    ))


def _erro_gemini_fatal(erro: Exception) -> bool:
    """Somente credencial/permissao invalida deve encerrar a cadeia inteira."""
    texto = _detalhe_erro_remoto(erro).upper()
    return any(indicador in texto for indicador in (
        "401", "403", "UNAUTHENTICATED", "PERMISSION_DENIED", "API KEY NOT VALID",
        "API_KEY_INVALID", "INCORRECT API KEY",
    ))


def _erro_nvidia_autenticacao(erro: Exception) -> bool:
    texto = _detalhe_erro_remoto(erro).upper()
    return any(indicador in texto for indicador in (
        "401", "UNAUTHORIZED", "INCORRECT API KEY", "INVALID API KEY",
        "INVALID AUTHORIZATION TOKEN",
    ))


def _erro_nvidia_bloqueio_rede(erro: Exception) -> bool:
    """Identifica bloqueio de borda, distinto de credencial ou cota."""
    texto = _detalhe_erro_remoto(erro).upper()
    return "ERROR CODE: 1010" in texto or "ACCESS DENIED" in texto


def _modelos_gemini() -> tuple[str, ...]:
    """Retorna no maximo tres modelos, sem repetir o modelo principal."""
    modelos: list[str] = []
    for modelo in (GEMINI_MODEL, *GEMINI_FALLBACK_MODELS):
        if modelo not in modelos:
            modelos.append(modelo)
    return tuple(modelos[:3])


def _aprovar_proposta_com_gemini(
    segmentos: list[dict], proposta: EditorialDecision
) -> EditorialDecision:
    """Faz fail-closed: sem Gemini valido, nenhum corte semantico e liberado."""
    if not proposta.discard_indexes:
        return proposta
    if not (GEMINI_API_KEY or NVIDIA_API_KEY):
        logger.warning("Nenhum aprovador remoto esta configurado; cortes do Ollama nao foram liberados.")
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
Quando os candidatos forem a primeira versao de uma abertura repetida apos uma
fala explicita de reinicio/correcao, aprove-os como "falsa_partida".
O mesmo vale para duas sequencias consecutivas quase identicas no inicio da aula.
Responda APENAS JSON valido: {"approve":[{"i":0,"reason":"erro|falsa_partida|autocorrecao|risada|conversa_lateral|devaneio|problema_tecnico"}]}.
Candidatos do Ollama:\n""" + json.dumps(candidatos, ensure_ascii=False) + "\nTranscricao completa:\n" + json.dumps(transcricao, ensure_ascii=False)
    try:
        resposta = solicitar_json_textual(prompt)
        bruto = json.loads(resposta)
        aprovados: set[int] = set()
        motivos: dict[int, str] = {}
        motivos_permitidos = {
            "erro", "falsa_partida", "autocorrecao", "risada",
            "conversa_lateral", "devaneio", "problema_tecnico", "comentario_bastidor",
        }
        for item in bruto.get("approve", []):
            indice, motivo = int(item["i"]), str(item["reason"])
            if indice in proposta.discard_indexes and motivo in motivos_permitidos:
                aprovados.add(indice)
                motivos[indice] = motivo
        return EditorialDecision(aprovados, motivos)
    except (GeminiReviewError, NvidiaReviewError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Nenhum provedor remoto aprovou cortes; proposta do Ollama foi bloqueada: %s", exc)
        return EditorialDecision(set(), {})


def _provedor_configurado(provedor: str) -> bool:
    return bool(GEMINI_API_KEY) if provedor == "gemini" else bool(NVIDIA_API_KEY)


def _solicitar_revisao_retake(prompt):
    """Revisao contextual comum a todas as rotas, inclusive modelos locais."""
    try:
        return solicitar_json_textual(prompt)
    except (GeminiReviewError, NvidiaReviewError):
        resposta = _request('POST', '/api/generate', {
            'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False, 'format': 'json',
            'options': {'temperature': 0, 'num_ctx': 8192},
        })
        return resposta['response']


def _validar_indices_resposta(bruto, permitidos):
    if not isinstance(bruto, dict) or 'discard' not in bruto:
        raise ValueError('Resposta editorial sem lista discard')
    for campo in ('discard', 'review'):
        itens = bruto.get(campo, [])
        if not isinstance(itens, list):
            raise ValueError('Lista editorial invalida')
        for item in itens:
            if not isinstance(item, dict) or type(item.get('i')) is not int or item['i'] not in permitidos:
                raise ValueError('Resposta editorial referencia indice inexistente')
            if campo == 'discard' and item.get('reason') not in MOTIVOS_CORTE_PERMITIDOS:
                raise ValueError('Motivo editorial invalido')


def _modelo_textual_atual(provedor: str) -> str | None:
    return ULTIMO_MODELO_GEMINI_TEXTUAL_USADO if provedor == "gemini" else ULTIMO_MODELO_NVIDIA_USADO


def solicitar_json_textual(prompt: str, provedor: str | None = None) -> str:
    """Obtém JSON de Gemini e NVIDIA, sem impedir o próximo fallback em falhas.

    Quando ``provedor`` e informado, tenta somente ele; isso preserva a ordem
    explícita da cadeia editorial e torna o diagnóstico rastreável.
    """
    provedores = (provedor,) if provedor else ("gemini", "nvidia")
    erros: list[str] = []
    for nome in provedores:
        if not _provedor_configurado(nome):
            continue
        try:
            return _gemini_request(prompt) if nome == "gemini" else _nvidia_request(prompt)
        except (GeminiReviewError, NvidiaReviewError) as exc:
            erros.append(f"{nome}: {exc}")
            logger.warning("%s textual falhou; tentando o proximo provedor.", nome.capitalize())
    if provedor == "nvidia":
        raise NvidiaReviewError("NVIDIA nao respondeu com JSON valido: " + " | ".join(erros))
    raise GeminiReviewError("Nenhum provedor textual respondeu com JSON valido: " + " | ".join(erros))


def _extrair_json_gemini_rest(resposta: dict) -> str:
    """Le a resposta final inteira, sem confundir blocos de pensamento com JSON."""
    candidatos = resposta.get("candidates") or []
    if not candidatos:
        raise ValueError("Gemini retornou HTTP 200 sem candidatos de resposta.")
    candidato = candidatos[0]
    motivo = candidato.get("finishReason")
    if motivo and motivo != "STOP":
        raise ValueError(f"Gemini retornou resposta incompleta ou bloqueada: {motivo}.")
    partes = candidato.get("content", {}).get("parts", [])
    texto = "".join(
        parte.get("text", "") for parte in partes if not parte.get("thought", False)
    ).strip()
    if not texto:
        raise ValueError("Gemini retornou HTTP 200 sem texto final.")
    json.loads(texto)
    return texto


def _gemini_request(prompt: str) -> str:
    """Solicita JSON, alternando modelos exceto em erro de chave/permissao."""
    if not GEMINI_API_KEY:
        raise GeminiReviewError("GEMINI_API_KEY ausente.")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
    }
    erros: list[str] = []
    global ULTIMO_MODELO_GEMINI_TEXTUAL_USADO
    for modelo in _modelos_gemini():
        try:
            resposta = _com_retry_remoto(lambda: _request_url(
                "POST", f"{GEMINI_URL}/{modelo}:generateContent", payload,
                {"x-goog-api-key": GEMINI_API_KEY}, timeout=120,
            ), modelo)
            uso = resposta.get("usageMetadata") or {}
            logger.info(
                "Gemini %s respondeu HTTP 200; resposta=%s; tokens entrada=%s, saida=%s, pensamento=%s, total=%s.",
                modelo, resposta.get("responseId", "nao informado"),
                uso.get("promptTokenCount"), uso.get("candidatesTokenCount"),
                uso.get("thoughtsTokenCount"), uso.get("totalTokenCount"),
            )
            texto = _extrair_json_gemini_rest(resposta)
            ULTIMO_MODELO_GEMINI_TEXTUAL_USADO = modelo
            return texto
        except Exception as exc:
            detalhe = _detalhe_erro_remoto(exc)
            erros.append(f"{modelo}: {type(exc).__name__}: {detalhe}")
            if _erro_gemini_fatal(exc) or _erro_gemini_quota_diaria(exc):
                if _erro_gemini_quota_diaria(exc):
                    logger.warning("Gemini atingiu a cota diaria; liberando o proximo provedor textual.")
                break
            logger.warning("Gemini textual %s falhou: %s; tentando o proximo modelo.", modelo, detalhe)
    raise GeminiReviewError("Nenhum modelo Gemini respondeu com JSON valido: " + " | ".join(erros))


def _nvidia_request(prompt: str) -> str:
    """Usa o endpoint Kimi/NVIDIA e valida o JSON antes de liberar qualquer corte."""
    if not NVIDIA_API_KEY:
        raise NvidiaReviewError("NVIDIA_API_KEY ausente.")
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": "Responda somente ao JSON solicitado, sem texto adicional."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 16384,
        "seed": 0,
        "stream": False,
        "reasoning_effort": NVIDIA_REASONING_EFFORT,
    }
    try:
        resposta = _com_retry_remoto(lambda: _request_url(
            "POST", f"{NVIDIA_BASE_URL}/chat/completions", payload,
            {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Accept": "application/json"}, timeout=180,
        ), NVIDIA_MODEL)
        uso = resposta.get("usage") or {}
        logger.info("NVIDIA %s respondeu; tokens entrada=%s, saida=%s, total=%s.",
                    NVIDIA_MODEL, uso.get("prompt_tokens"), uso.get("completion_tokens"), uso.get("total_tokens"))
        escolha = resposta["choices"][0]
        if escolha.get("finish_reason") not in (None, "stop"):
            raise ValueError(f"Resposta NVIDIA incompleta: {escolha.get('finish_reason')}")
        conteudo = escolha["message"].get("content")
        if isinstance(conteudo, list):
            conteudo = "".join(str(parte.get("text", "")) for parte in conteudo if isinstance(parte, dict))
        texto = str(conteudo or "").strip()
        bloco = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", texto, flags=re.DOTALL)
        if bloco:
            texto = bloco.group(1)
        if not isinstance(json.loads(texto), dict):
            raise ValueError("NVIDIA deve retornar um objeto JSON.")
        global ULTIMO_MODELO_NVIDIA_USADO
        ULTIMO_MODELO_NVIDIA_USADO = NVIDIA_MODEL
        return texto
    except Exception as exc:
        detalhe = _detalhe_erro_remoto(exc)
        if _erro_nvidia_autenticacao(exc):
            raise NvidiaReviewError(
                "Autenticacao NVIDIA recusada. Substitua NVIDIA_API_KEY no .env por uma chave valida do console NVIDIA e reinicie o backend."
            ) from exc
        raise NvidiaReviewError(f"{NVIDIA_MODEL}: {type(exc).__name__}: {detalhe}") from exc


def _decidir_lote(lote: list[dict]) -> dict[int, str]:
    transcricao = [
        {"i": indice, "inicio": round(float(item["start"]), 2), "fim": round(float(item["end"]), 2), "texto": str(item["text"]).strip()}
        for indice, item in enumerate(lote)
    ]
    prompt = """Voce e o revisor de uma videoaula em portugues. Decida quais trechos devem ser removidos na pre-edicao.
Remova SOMENTE: erros declarados pelo professor, falsas partidas que ele regrava, autocorrecoes cuja versao correta vem logo depois, risadas/conversas laterais, problemas tecnicos e devaneios claramente desconectados do assunto da aula.
RETAKE: quando a mesma abertura ou frase aparece de novo apos o professor dizer que vai recomecar, cortar ou que falou errado, descarte a primeira versao como "falsa_partida" e mantenha a ultima.
No inicio da aula, duas sequencias consecutivas quase identicas tambem indicam retake, mesmo sem aviso verbal.
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
