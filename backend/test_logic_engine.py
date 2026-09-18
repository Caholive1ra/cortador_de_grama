"""Testes unitários do motor de classificação V1/V2."""

from logic_engine import classificar_segmentos


def test_classifica_gatilho_errei_como_v1() -> None:
    segmentos = [
        {"start": 0.0, "end": 1.5, "text": "Errei essa fala, desculpa."},
    ]

    resultado = classificar_segmentos(segmentos)

    assert len(resultado) == 1
    assert resultado[0]["track"] == "V1"
    assert resultado[0]["start"] == 0.0
    assert resultado[0]["end"] == 1.5
    assert resultado[0]["text"] == "Errei essa fala, desculpa."


def test_classifica_texto_normal_como_v2() -> None:
    segmentos = [
        {"start": 0.0, "end": 2.0, "text": "Hoje vamos falar sobre edição."},
    ]

    resultado = classificar_segmentos(segmentos)

    assert len(resultado) == 1
    assert resultado[0]["track"] == "V2"
    assert resultado[0]["text"] == "Hoje vamos falar sobre edição."


def test_insere_segmento_artificial_de_silencio() -> None:
    segmentos = [
        {"start": 0.0, "end": 1.0, "text": "Primeira fala limpa."},
        {"start": 4.5, "end": 6.0, "text": "Continuação da aula."},
    ]

    resultado = classificar_segmentos(segmentos)

    assert len(resultado) == 3
    silencio = resultado[1]
    assert silencio["text"] == "[SILÊNCIO]"
    assert silencio["track"] == "V1"
    assert silencio["start"] == 1.0
    assert silencio["end"] == 4.5
    assert resultado[0]["track"] == "V2"
    assert resultado[2]["track"] == "V2"
