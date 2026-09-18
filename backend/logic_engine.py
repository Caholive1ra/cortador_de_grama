"""Motor de regras para classificar segmentos em trilhas V1/V2."""

import logging

logger = logging.getLogger(__name__)

GATILHOS_ERRO = ["errei", "desculpa", "corta", "vou repetir", "de novo"]
LIMITE_SILENCIO_SEGUNDOS = 2.0


def classificar_segmentos(segmentos: list[dict]) -> list[dict]:
    """Classifica segmentos de transcrição em V1 (erro/silêncio) ou V2 (fala limpa).

    Args:
        segmentos: Saída do transcritor, no formato
            ``[{"start": float, "end": float, "text": str}, ...]``.

    Returns:
        Nova lista com a chave ``track`` em cada item, incluindo segmentos
        artificiais de silêncio quando a lacuna entre falas for maior que 2s.
    """
    classificados: list[dict] = []
    fim_anterior: float | None = None

    for segmento in segmentos:
        inicio = float(segmento["start"])
        fim = float(segmento["end"])
        texto = str(segmento["text"])

        if fim_anterior is not None:
            lacuna = inicio - fim_anterior
            if lacuna > LIMITE_SILENCIO_SEGUNDOS:
                classificados.append(
                    {
                        "start": fim_anterior,
                        "end": inicio,
                        "text": "[SILÊNCIO]",
                        "track": "V1",
                    }
                )

        texto_normalizado = texto.lower()
        track = "V1" if _contem_gatilho(texto_normalizado) else "V2"
        classificados.append(
            {
                "start": inicio,
                "end": fim,
                "text": texto,
                "track": track,
            }
        )
        fim_anterior = fim

    total_v1 = sum(1 for item in classificados if item["track"] == "V1")
    total_v2 = sum(1 for item in classificados if item["track"] == "V2")
    logger.info(
        "Classificação concluída. Trechos V1 (erros/silêncios): %s. Trechos V2 (acertos): %s.",
        total_v1,
        total_v2,
    )
    return classificados


def _contem_gatilho(texto_normalizado: str) -> bool:
    """Indica se o texto contém alguma palavra-gatilho de erro."""
    return any(gatilho in texto_normalizado for gatilho in GATILHOS_ERRO)
