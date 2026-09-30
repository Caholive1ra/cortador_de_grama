"""Motor de análise editorial para classificar fala útil e descarte."""

import logging
import re
import unicodedata
from difflib import SequenceMatcher

from editorial_ai import EditorialDecision, decidir_cortes_semanticos

logger = logging.getLogger(__name__)

GATILHOS_ERRO = (
    "errei", "falei errado", "pode cortar", "vou recomeçar",
    "não ficou bom", "vamos novamente", "começar de novo",
)
LIMITE_SILENCIO_SEGUNDOS = 1.0
JANELA_REPETICAO_SEGUNDOS = 15.0
SIMILARIDADE_REPETICAO = 0.72
MINIMO_PALAVRAS_REPETICAO = 3
JANELA_MICROSEGMENTO_SEGUNDOS = 2.0
GATILHOS_ERRO += ("ficou ruim essa parte", "deixa eu voltar essa parte")
GATILHOS_ERRO += (
    "deixa eu ver um gancho", "deixa eu pegar um gancho",
    "vou procurar um gancho", "isso a gente corta depois",
)


def classificar_segmentos(
    segmentos: list[dict],
    duracao_total: float | None = None,
    revisao_semantica: bool = True,
    audio_path: str | None = None,
    modo_economico: bool = False,
) -> list[dict]:
    """Classifica e normaliza os segmentos em uma linha do tempo contínua.

    Quando ``duracao_total`` é informada, o resultado cobre exatamente o
    intervalo entre zero e o fim da mídia. Pausas curtas permanecem habilitadas;
    silêncios de um segundo ou mais são marcados para descarte.
    """
    segmentos = _criar_microsegmentos(segmentos)
    decisao = (
        decidir_cortes_semanticos(
            segmentos, audio_path=audio_path, modo_economico=modo_economico
        )
        if revisao_semantica and segmentos
        else EditorialDecision(set(), {})
    )
    decisao = _validar_decisao_semantica(segmentos, decisao)
    classificados: list[dict] = []
    indice_fala_anterior: int | None = None
    fim_anterior = 0.0

    for indice_original, segmento in sorted(
        enumerate(segmentos), key=lambda item: float(item[1]["start"])
    ):
        inicio = max(0.0, float(segmento["start"]))
        fim = float(segmento["end"])
        if duracao_total is not None:
            inicio = min(inicio, duracao_total)
            fim = min(fim, duracao_total)
        inicio = max(inicio, fim_anterior)
        texto = str(segmento["text"]).strip()

        if fim <= inicio:
            logger.warning("Segmento inválido ignorado: %s", segmento)
            continue

        if inicio > fim_anterior:
            duracao_pausa = inicio - fim_anterior
            descarte = duracao_pausa >= LIMITE_SILENCIO_SEGUNDOS
            classificados.append({
                "start": fim_anterior,
                "end": inicio,
                "text": "[SILÊNCIO]" if descarte else "[PAUSA CURTA]",
                "track": "V1" if descarte else "V2",
                "enabled": not descarte,
                "reason": "silencio" if descarte else "pausa_curta",
            })

        texto_normalizado = _normalizar_texto(texto)
        motivo_semantico = decisao.reasons.get(indice_original)
        revisar = indice_original in decisao.review_indexes
        contem_gatilho = _contem_gatilho(texto_normalizado) or motivo_semantico is not None
        atual = {
            "start": inicio,
            "end": fim,
            "text": texto,
            "track": "V1" if contem_gatilho else "V2",
            "enabled": not contem_gatilho,
            "reason": motivo_semantico or ("comando_de_corte" if contem_gatilho else "fala_util"),
            "review": revisar,
            "review_reason": decisao.review_reasons.get(indice_original) if revisar else None,
        }

        classificados.append(atual)
        indice_fala_anterior = len(classificados) - 1
        fim_anterior = fim

    if duracao_total is not None and fim_anterior < duracao_total:
        duracao_pausa = duracao_total - fim_anterior
        descarte = duracao_pausa >= LIMITE_SILENCIO_SEGUNDOS
        classificados.append({
            "start": fim_anterior,
            "end": duracao_total,
            "text": "[SILÊNCIO]" if descarte else "[PAUSA CURTA]",
            "track": "V1" if descarte else "V2",
            "enabled": not descarte,
            "reason": "silencio" if descarte else "pausa_curta",
        })

    total_v1 = sum(1 for item in classificados if item["track"] == "V1")
    total_v2 = sum(1 for item in classificados if item["track"] == "V2")
    logger.info("Análise concluída. Descartes: %s. Trechos úteis: %s.", total_v1, total_v2)
    return classificados


def _criar_microsegmentos(segmentos: list[dict]) -> list[dict]:
    """Divide falas longas usando timestamps de palavras para cortes precisos."""
    resultado: list[dict] = []
    for segmento in segmentos:
        palavras = segmento.get("words") or []
        if not palavras:
            resultado.append(segmento)
            continue
        lote: list[dict] = []
        inicio_lote: float | None = None
        for palavra in palavras:
            inicio = float(palavra["start"])
            if inicio_lote is not None and inicio - inicio_lote >= JANELA_MICROSEGMENTO_SEGUNDOS:
                resultado.append(_montar_microsegmento(lote))
                lote, inicio_lote = [], None
            lote.append(palavra)
            inicio_lote = inicio if inicio_lote is None else inicio_lote
        if lote:
            resultado.append(_montar_microsegmento(lote))
    return resultado


def _montar_microsegmento(palavras: list[dict]) -> dict:
    return {
        "start": float(palavras[0]["start"]),
        "end": float(palavras[-1]["end"]),
        "text": " ".join(str(palavra["word"]).strip() for palavra in palavras).strip(),
        "words": palavras,
    }


def _validar_decisao_semantica(
    segmentos: list[dict], decisao: EditorialDecision
) -> EditorialDecision:
    """Mantém somente índices válidos devolvidos pelo revisor.

    Não limitamos a proporção total de cortes: em uma gravação bruta, uma
    abertura longa com conversa, retakes e pausas pode legitimamente ocupar a
    maior parte da fala transcrita. A validação editorial fica a cargo do
    Gemini, que recebe o áudio original, e o XML continua sendo revisável no
    Premiere antes da exportação final.
    """
    if not segmentos or not decisao.reasons:
        return decisao

    indices_validos = {
        indice for indice in decisao.reasons if 0 <= indice < len(segmentos)
    }
    if len(indices_validos) == len(segmentos):
        logger.warning(
            "Revisor semantico sugeriu remover toda a fala; decisao ignorada por seguranca."
        )
        return EditorialDecision(set(), {})
    return EditorialDecision(indices_validos, {
        indice: motivo
        for indice, motivo in decisao.reasons.items()
        if indice in indices_validos
    })


def _normalizar_texto(texto: str) -> str:
    """Remove acentos, pontuação e diferenças de caixa para comparação."""
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(caractere) != "Mn"
    )
    return " ".join(re.findall(r"[a-z0-9]+", sem_acentos))


def _contem_gatilho(texto_normalizado: str) -> bool:
    """Indica se o texto contém algum comando explícito de erro/corte."""
    return any(
        _normalizar_texto(gatilho) in texto_normalizado
        for gatilho in GATILHOS_ERRO
    )


def _parece_nova_tentativa(texto_anterior: str, texto_atual: str) -> bool:
    """Detecta repetição integral ou retomada ampliada de uma frase."""
    anterior = _normalizar_texto(texto_anterior)
    atual = _normalizar_texto(texto_atual)
    palavras_anterior = anterior.split()
    palavras_atual = atual.split()

    if min(len(palavras_anterior), len(palavras_atual)) < MINIMO_PALAVRAS_REPETICAO:
        return False

    similaridade = SequenceMatcher(None, anterior, atual).ratio()
    if len(palavras_anterior) <= len(palavras_atual):
        menor, maior = palavras_anterior, palavras_atual
    else:
        menor, maior = palavras_atual, palavras_anterior
    retomada_por_prefixo = menor == maior[: len(menor)]
    return similaridade >= SIMILARIDADE_REPETICAO or retomada_por_prefixo
