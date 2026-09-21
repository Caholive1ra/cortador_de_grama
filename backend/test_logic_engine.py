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


def test_silencio_de_exatamente_um_segundo_e_descartado() -> None:
    segmentos = [
        {"start": 0.0, "end": 1.0, "text": "Primeira fala."},
        {"start": 2.0, "end": 3.0, "text": "Segunda fala."},
    ]

    resultado = classificar_segmentos(segmentos)

    assert resultado[1]["text"] == "[SILÊNCIO]"
    assert resultado[1]["reason"] == "silencio"


def test_mantem_segunda_tentativa_de_uma_repeticao() -> None:
    segmentos = [
        {"start": 0.0, "end": 2.0, "text": "Hoje vamos estudar edição de vídeo"},
        {"start": 2.4, "end": 4.5, "text": "Hoje vamos estudar edição de vídeos"},
    ]

    resultado = classificar_segmentos(segmentos)

    assert resultado[0]["track"] == "V1"
    assert resultado[0]["reason"] == "tentativa_substituida"
    assert resultado[1]["reason"] == "pausa_curta"
    assert resultado[2]["track"] == "V2"
    assert resultado[2]["reason"] == "ultima_tentativa"


def test_descarta_falsa_partida_quando_frase_e_recomecada() -> None:
    segmentos = [
        {"start": 0.0, "end": 1.0, "text": "O nosso objetivo principal"},
        {"start": 1.3, "end": 4.0, "text": "O nosso objetivo principal é compreender a ferramenta"},
    ]

    resultado = classificar_segmentos(segmentos)

    assert resultado[0]["track"] == "V1"
    assert resultado[1]["track"] == "V2"


def test_gatilho_com_acento_e_normalizado() -> None:
    resultado = classificar_segmentos(
        [{"start": 0.0, "end": 1.0, "text": "Não ficou bom, vou recomeçar."}]
    )

    assert resultado[0]["track"] == "V1"
    assert resultado[0]["reason"] == "comando_de_corte"


def test_timeline_cobre_do_zero_ate_a_duracao_total_sem_gaps() -> None:
    resultado = classificar_segmentos(
        [{"start": 0.5, "end": 2.0, "text": "Conteúdo da aula."}],
        duracao_total=4.0,
    )

    assert resultado[0]["start"] == 0.0
    assert resultado[-1]["end"] == 4.0
    assert all(
        atual["end"] == seguinte["start"]
        for atual, seguinte in zip(resultado, resultado[1:])
    )
