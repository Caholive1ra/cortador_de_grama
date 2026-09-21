"""Motor de análise editorial para classificar fala útil e descarte."""

import logging
import re
import unicodedata
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

GATILHOS_ERRO = (
    "errei", "desculpa", "corta", "pode cortar", "vou repetir",
    "vou recomeçar", "de novo", "mais uma vez", "não ficou bom",
    "vamos novamente", "volta", "retoma",
)
LIMITE_SILENCIO_SEGUNDOS = 1.0
JANELA_REPETICAO_SEGUNDOS = 15.0
SIMILARIDADE_REPETICAO = 0.72
MINIMO_PALAVRAS_REPETICAO = 3


def classificar_segmentos(
    segmentos: list[dict],
    duracao_total: float | None = None,
) -> list[dict]:
    """Classifica e normaliza os segmentos em uma linha do tempo contínua.

    Quando ``duracao_total`` é informada, o resultado cobre exatamente o
    intervalo entre zero e o fim da mídia. Pausas curtas permanecem habilitadas;
    silêncios de um segundo ou mais são marcados para descarte.
    """
    classificados: list[dict] = []
    indice_fala_anterior: int | None = None
    fim_anterior = 0.0

    for segmento in sorted(segmentos, key=lambda item: float(item["start"])):
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
        contem_gatilho = _contem_gatilho(texto_normalizado)
        atual = {
            "start": inicio,
            "end": fim,
            "text": texto,
            "track": "V1" if contem_gatilho else "V2",
            "enabled": not contem_gatilho,
            "reason": "comando_de_corte" if contem_gatilho else "fala_util",
        }

        if indice_fala_anterior is not None and not contem_gatilho:
            anterior = classificados[indice_fala_anterior]
            distancia = inicio - float(anterior["end"])
            if (
                distancia <= JANELA_REPETICAO_SEGUNDOS
                and _parece_nova_tentativa(str(anterior["text"]), texto)
            ):
                anterior["track"] = "V1"
                anterior["enabled"] = False
                anterior["reason"] = "tentativa_substituida"
                atual["reason"] = "ultima_tentativa"

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
