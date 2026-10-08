
"""Contratos do avaliador semântico local.

Objetivo editorial:
Identificar tentativas de explicação descartadas pelo professor
e preservar a versão correta, completa e contextualizada.

Não remover vícios de linguagem, hesitações naturais ou
repetições que tenham função didática.
"""

import json
import sys
import types

import pytest

from editorial_ai import (
    EditorialModelError,
    GeminiReviewError,
    LayaReviewError,
    decidir_cortes_semanticos,
    verificar_modelo_editorial,
)

from logic_engine import classificar_segmentos


# ==========================================================
# FUNÇÕES AUXILIARES
# ==========================================================

def segmento(inicio, fim, texto):
    return {
        "start": inicio,
        "end": fim,
        "text": texto,
    }


def simular_modelo(monkeypatch, descartes):

    respostas = iter([
        {"models": [{"name": "qwen2.5:3b"}]},
        {
            "response": json.dumps({
                "discard": descartes
            })
        },
    ])

    monkeypatch.setattr(
        "editorial_ai._request",
        lambda *args, **kwargs: next(respostas),
    )
    monkeypatch.setattr(
        "editorial_ai._aprovar_proposta_com_gemini",
        lambda segmentos, proposta: proposta,
    )
    monkeypatch.setattr(
        "editorial_ai._decidir_cortes_com_laya",
        lambda segmentos: (_ for _ in ()).throw(LayaReviewError("isolado no teste do Ollama")),
    )


# ==========================================================
# TESTES ORIGINAIS
# ==========================================================

def test_rejeita_ausencia_do_modelo(monkeypatch):

    monkeypatch.setattr(
        "editorial_ai._request",
        lambda *args, **kwargs: {"models": []},
    )

    with pytest.raises(
        EditorialModelError,
        match="nao esta instalado",
    ):
        verificar_modelo_editorial()


def test_aplica_somente_indices_e_motivos_permitidos(
    monkeypatch,
):

    simular_modelo(monkeypatch, [
        {"i": 0, "reason": "falsa_partida"},
        {"i": 1, "reason": "assunto_ruim"},
        {"i": 99, "reason": "risada"},
    ])

    decisao = decidir_cortes_semanticos([
        segmento(0, 1, "Vou começar de novo."),
        segmento(1, 2, "Conteúdo útil."),
    ])

    assert decisao.discard_indexes == {0}

    assert decisao.reasons == {
        0: "falsa_partida"
    }


def test_logica_mantem_conteudo_quando_modelo_nao_marca(
    monkeypatch,
):

    monkeypatch.setattr(
        "logic_engine.decidir_cortes_semanticos",
        lambda segmentos, **kwargs: type(
            "Decisao",
            (),
            {"reasons": {1: "devaneio"}},
        )(),
    )

    resultado = classificar_segmentos([
        segmento(
            0, 1,
            "Agora explicamos o conceito.",
        ),
        segmento(
            1, 2,
            "Minha viagem ontem foi muito longa.",
        ),
    ])

    assert resultado[0]["enabled"] is True
    assert resultado[1]["enabled"] is False
    assert resultado[1]["reason"] == "devaneio"


# ==========================================================
# ERROS SEMÂNTICOS E AUTOCORREÇÕES
# ==========================================================

def test_remove_explicacao_errada_e_mantem_versao_correta(
    monkeypatch,
):
    """A explicação abandonada deve ser removida."""

    segmentos = [
        segmento(
            0, 5,
            "Uma variável int pode armazenar textos.",
        ),
        segmento(
            5, 7,
            "Não, pera aí, falei errado.",
        ),
        segmento(
            7, 12,
            "Uma variável int armazena números inteiros.",
        ),
    ]

    simular_modelo(monkeypatch, [
        {"i": 0, "reason": "falsa_partida"},
        {"i": 1, "reason": "falsa_partida"},
    ])

    decisao = decidir_cortes_semanticos(segmentos)

    assert decisao.discard_indexes == {0, 1}
    assert 2 not in decisao.discard_indexes


def test_remove_tentativa_abandonada_e_mantem_regravacao(
    monkeypatch,
):
    """Remove uma tentativa substituída por uma regravação."""

    segmentos = [
        segmento(
            0, 4,
            "Agora vamos falar sobre herança.",
        ),
        segmento(
            4, 7,
            "Herança é quando uma classe... não, pera.",
        ),
        segmento(
            7, 10,
            "Vou explicar novamente.",
        ),
        segmento(
            10, 15,
            "Agora vamos falar sobre herança.",
        ),
        segmento(
            15, 20,
            "Herança permite que uma classe herde "
            "características de outra classe.",
        ),
    ]

    simular_modelo(monkeypatch, [
        {"i": 0, "reason": "falsa_partida"},
        {"i": 1, "reason": "falsa_partida"},
        {"i": 2, "reason": "falsa_partida"},
    ])

    decisao = decidir_cortes_semanticos(segmentos)

    assert decisao.discard_indexes == {0, 1, 2}

    assert 3 not in decisao.discard_indexes
    assert 4 not in decisao.discard_indexes


def test_remove_repeticao_acidental(
    monkeypatch,
):
    """Remove uma tentativa repetida sem valor didático."""

    segmentos = [
        segmento(
            0, 5,
            "O método recebe dois parâmetros.",
        ),
        segmento(
            5, 8,
            "Não, deixa eu começar de novo.",
        ),
        segmento(
            8, 13,
            "O método recebe dois parâmetros.",
        ),
        segmento(
            13, 17,
            "O primeiro representa o nome e o segundo a idade.",
        ),
    ]

    simular_modelo(monkeypatch, [
        {"i": 0, "reason": "falsa_partida"},
        {"i": 1, "reason": "falsa_partida"},
    ])

    decisao = decidir_cortes_semanticos(segmentos)

    assert decisao.discard_indexes == {0, 1}


def test_regra_local_remove_primeira_abertura_repetida_apos_reinicio(monkeypatch):
    """Mesmo se a IA mantiver tudo, retake explícito não pode sobreviver."""
    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "editorial_ai._gemini_audio_request",
        lambda prompt, audio_path: '{"discard": [], "review": []}',
    )
    segmentos = [
        segmento(0, 3, "Olá, nesta aula vamos entender a arquitetura do sistema."),
        segmento(3, 5, "Não, vou recomeçar porque falei errado."),
        segmento(5, 8, "Olá, nesta aula vamos entender a arquitetura do sistema."),
        segmento(8, 10, "Primeiro vamos falar sobre os componentes."),
    ]
    decisao = decidir_cortes_semanticos(segmentos, audio_path="aula.wav")
    assert decisao.discard_indexes == {0}
    assert decisao.reasons == {0: "falsa_partida"}


def test_regra_local_preserva_repeticao_sem_aviso_de_reinicio(monkeypatch):
    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "editorial_ai._gemini_audio_request",
        lambda prompt, audio_path: '{"discard": [], "review": []}',
    )
    segmentos = [
        segmento(0, 3, "A arquitetura separa responsabilidades do sistema."),
        segmento(3, 5, "Isso é importante para manutenção."),
        segmento(5, 8, "A arquitetura separa responsabilidades do sistema."),
    ]
    decisao = decidir_cortes_semanticos(segmentos, audio_path="aula.wav")
    assert decisao.discard_indexes == set()


def test_regra_local_remove_duas_sequencias_iguais_no_inicio_sem_aviso(monkeypatch):
    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "editorial_ai._gemini_audio_request",
        lambda prompt, audio_path: '{"discard": [], "review": []}',
    )
    segmentos = [
        segmento(0, 3, "Olá, hoje vamos aprender os fundamentos da arquitetura."),
        segmento(3, 6, "Eu sou Arnaldo e vou conduzir esta aula para vocês."),
        segmento(6, 7, ""),
        segmento(7, 10, "Olá, hoje vamos aprender os fundamentos da arquitetura."),
        segmento(10, 13, "Eu sou Arnaldo e vou conduzir esta aula para vocês."),
    ]
    decisao = decidir_cortes_semanticos(segmentos, audio_path="aula.wav")
    assert decisao.discard_indexes == {0, 1}


def test_laya_mantem_motivo_de_falsa_partida_confirmado(monkeypatch):
    from editorial_ai import _decidir_cortes_com_laya

    class RouterFalso:
        def predict(self, *_args, **_kwargs):
            return {"answers": {"cut": {"choice": "discard"}}}

    monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=RouterFalso))
    segmentos = [
        segmento(0, 3, "Olá, nesta aula vamos entender a arquitetura do sistema."),
        segmento(3, 5, "Não, vou recomeçar porque falei errado."),
        segmento(5, 8, "Olá, nesta aula vamos entender a arquitetura do sistema."),
    ]
    decisao = _decidir_cortes_com_laya(segmentos)
    assert decisao.reasons[0] == "falsa_partida"

    assert 2 not in decisao.discard_indexes
    assert 3 not in decisao.discard_indexes


# ==========================================================
# PRESERVAÇÃO DO CONTEXTO DIDÁTICO
# ==========================================================

def test_preserva_vicios_de_linguagem(
    monkeypatch,
):
    """Vícios de linguagem não justificam cortes isolados."""

    segmentos = [
        segmento(
            0, 5,
            "Então, ééé, uma variável armazena um valor.",
        ),
        segmento(
            5, 10,
            "E aí, né, a gente pode utilizar esse valor.",
        ),
    ]

    simular_modelo(monkeypatch, [])

    decisao = decidir_cortes_semanticos(segmentos)

    assert decisao.discard_indexes == set()


def test_preserva_repeticao_didatica(
    monkeypatch,
):
    """Repetir um conceito para reforçá-lo não é um erro."""

    segmentos = [
        segmento(
            0, 5,
            "Uma constante não pode ser reatribuída.",
        ),
        segmento(
            5, 10,
            "Vou repetir porque isso é importante.",
        ),
        segmento(
            10, 15,
            "Uma constante não pode ser reatribuída.",
        ),
    ]

    simular_modelo(monkeypatch, [])

    decisao = decidir_cortes_semanticos(segmentos)

    assert decisao.discard_indexes == set()


def test_preserva_exemplo_com_erro_intencional(
    monkeypatch,
):
    """Erros usados para ensinar não devem ser removidos."""

    segmentos = [
        segmento(
            0, 5,
            "Observe este código com um erro de sintaxe.",
        ),
        segmento(
            5, 10,
            "Estamos tentando atribuir uma string a um int.",
        ),
        segmento(
            10, 15,
            "O compilador vai apresentar um erro de tipo.",
        ),
    ]

    simular_modelo(monkeypatch, [])

    decisao = decidir_cortes_semanticos(segmentos)

    assert decisao.discard_indexes == set()


def test_preserva_correcao_que_ensina_um_conceito(
    monkeypatch,
):
    """Uma correção didática faz parte da explicação."""

    segmentos = [
        segmento(
            0, 5,
            "Muitas pessoas acreditam que Java "
            "é interpretado diretamente.",
        ),
        segmento(
            5, 10,
            "Na verdade, o código é compilado "
            "para bytecode.",
        ),
        segmento(
            10, 15,
            "Depois, a JVM executa esse bytecode.",
        ),
    ]

    simular_modelo(monkeypatch, [])

    decisao = decidir_cortes_semanticos(segmentos)

    assert decisao.discard_indexes == set()


def test_preserva_continuacao_de_explicacao(
    monkeypatch,
):
    """Uma explicação dividida em segmentos deve ser mantida."""

    segmentos = [
        segmento(
            0, 5,
            "Para utilizar uma interface, primeiro...",
        ),
        segmento(
            5, 10,
            "precisamos declarar os métodos necessários.",
        ),
        segmento(
            10, 15,
            "Depois implementamos esses métodos na classe.",
        ),
    ]

    simular_modelo(monkeypatch, [])

    decisao = decidir_cortes_semanticos(segmentos)

    assert decisao.discard_indexes == set()

    
def test_nao_remove_aula_inteira(monkeypatch):
    """Uma decisão que elimina toda a aula deve ser rejeitada."""

    segmentos = [
        {
            "start": 0,
            "end": 5,
            "text": "Hoje vamos aprender sobre variáveis.",
        },
        {
            "start": 5,
            "end": 10,
            "text": "Uma variável armazena um valor.",
        },
        {
            "start": 10,
            "end": 15,
            "text": "Podemos declarar uma variável do tipo int.",
        },
    ]

    monkeypatch.setattr(
        "logic_engine.decidir_cortes_semanticos",
        lambda segmentos, **kwargs: type(
            "Decisao",
            (),
            {
                "reasons": {
                    0: "repeticao",
                    1: "falsa_partida",
                    2: "repeticao",
                }
            },
        )(),
    )

    resultado = classificar_segmentos(segmentos)

    assert any(
        item["enabled"] is True
        for item in resultado
    ), "ERRO: o sistema removeu todos os segmentos da aula."


def test_gemini_so_aprova_indices_propostos(monkeypatch):
    from editorial_ai import EditorialDecision, _aprovar_proposta_com_gemini

    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "editorial_ai._gemini_request",
        lambda prompt: json.dumps({"approve": [
            {"i": 0, "reason": "erro"},
            {"i": 1, "reason": "falsa_partida"},
        ]}),
    )
    resultado = _aprovar_proposta_com_gemini(
        [segmento(0, 1, "Erro"), segmento(1, 2, "Conteudo")],
        EditorialDecision({0}, {0: "erro"}),
    )
    assert resultado.discard_indexes == {0}
    assert resultado.reasons == {0: "erro"}


def test_sem_chave_gemini_bloqueia_cortes_semanticos(monkeypatch):
    from editorial_ai import EditorialDecision, _aprovar_proposta_com_gemini

    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", None)
    resultado = _aprovar_proposta_com_gemini(
        [segmento(0, 1, "Erro")], EditorialDecision({0}, {0: "erro"})
    )
    assert resultado.discard_indexes == set()


def test_gemini_multimodal_pode_marcar_risada(monkeypatch):
    from editorial_ai import decidir_cortes_semanticos

    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "editorial_ai._gemini_audio_request",
        lambda prompt, audio_path: json.dumps({"discard": [
            {"i": 0, "reason": "risada"},
        ]}),
    )
    resultado = decidir_cortes_semanticos(
        [segmento(0, 2, ""), segmento(2, 4, "Vamos iniciar a aula.")],
        audio_path="aula.wav",
    )
    assert resultado.discard_indexes == {0}
    assert resultado.reasons == {0: "risada"}


def test_gemini_multimodal_marca_duvida_para_revisao(monkeypatch):
    from editorial_ai import decidir_cortes_semanticos

    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "editorial_ai._gemini_audio_request",
        lambda prompt, audio_path: json.dumps({"discard": [], "review": [
            {"i": 0, "reason": "duvida_editorial"}
        ]}),
    )
    resultado = decidir_cortes_semanticos([segmento(0, 1, "Deixa eu ver.")], "audio.wav")
    assert resultado.discard_indexes == set()
    assert resultado.review_indexes == {0}


def test_identifica_indisponibilidade_temporaria_do_gemini() -> None:
    from editorial_ai import _gemini_esta_sobrecarregado

    assert _gemini_esta_sobrecarregado(RuntimeError("503 UNAVAILABLE"))
    assert not _gemini_esta_sobrecarregado(RuntimeError("429 RESOURCE_EXHAUSTED"))


def test_prioriza_tres_modelos_gemini_sem_repeticao(monkeypatch) -> None:
    from editorial_ai import _modelos_gemini

    monkeypatch.setattr("editorial_ai.GEMINI_MODEL", "gemini-3.5-flash")
    monkeypatch.setattr(
        "editorial_ai.GEMINI_FALLBACK_MODELS",
        ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.7-flash"),
    )
    assert _modelos_gemini() == (
        "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash",
    )


def test_gemini_textual_tenta_proximo_modelo_em_erro_de_modelo(monkeypatch) -> None:
    from editorial_ai import _gemini_request, obter_ultimo_modelo_gemini_textual

    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "editorial_ai._modelos_gemini",
        lambda: ("gemini-3.8-flash", "gemini-3.7-flash"),
    )
    chamadas = []

    def responder(_method, url, *_args, **_kwargs):
        chamadas.append(url)
        if "gemini-3.8-flash" in url:
            raise RuntimeError("400 INVALID_ARGUMENT: modelo indisponivel")
        return {"candidates": [{"content": {"parts": [{"text": '{"approve": []}'}]}}]}

    monkeypatch.setattr("editorial_ai._request_url", responder)
    assert _gemini_request("teste") == '{"approve": []}'
    assert chamadas == [
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent",
    ]
    assert obter_ultimo_modelo_gemini_textual() == "gemini-3.7-flash"


def test_modo_economico_envia_apenas_candidatos_e_contexto_curto(monkeypatch) -> None:
    from editorial_ai import decidir_cortes_semanticos, obter_diagnostico_editorial

    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    prompts = []
    monkeypatch.setattr("editorial_ai._gemini_request", lambda prompt: prompts.append(prompt) or '{"discard": [{"i": 10, "reason": "erro"}], "review": []}')
    segmentos = [segmento(indice, indice + 1, "Explicacao didatica comum.") for indice in range(30)]
    segmentos[10] = segmento(10, 11, "Errei, vou recomecar esta parte.")

    resultado = decidir_cortes_semanticos(segmentos, modo_economico=True)
    assert resultado.discard_indexes == {10}
    assert len(prompts) == 1
    assert '"candidate_id": 10' in prompts[0]
    assert "candidate_id\": 29" not in prompts[0]
    assert obter_diagnostico_editorial()["mode"] == "gemini_economic"


def test_nvidia_e_usado_quando_gemini_textual_falha(monkeypatch) -> None:
    from editorial_ai import solicitar_json_textual

    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "gemini")
    monkeypatch.setattr("editorial_ai.NVIDIA_API_KEY", "nvidia")
    monkeypatch.setattr("editorial_ai._gemini_request", lambda prompt: (_ for _ in ()).throw(GeminiReviewError("indisponivel")))
    monkeypatch.setattr("editorial_ai._nvidia_request", lambda prompt: '{"approve": []}')
    assert solicitar_json_textual("teste") == '{"approve": []}'


def test_nvidia_fallback_completo_usa_contrato_editorial_unificado(monkeypatch) -> None:
    from editorial_ai import decidir_cortes_semanticos, obter_diagnostico_editorial

    prompts = []
    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "")
    monkeypatch.setattr("editorial_ai.NVIDIA_API_KEY", "nvidia")
    monkeypatch.setattr(
        "editorial_ai._nvidia_request",
        lambda prompt: prompts.append(prompt) or '{"discard": [{"i": 1, "reason": "comentario_bastidor"}], "review": []}',
    )
    monkeypatch.setattr("editorial_ai._aplicar_retakes_explicitos", lambda _segmentos, decisao: decisao)

    resultado = decidir_cortes_semanticos([
        segmento(0, 2, "Explicacao didatica que deve permanecer."),
        segmento(2, 4, "Deixa eu ver um gancho aqui."),
    ], audio_path="aula.wav")

    assert resultado.discard_indexes == {1}
    assert '"i": 0' in prompts[0]
    assert '"i": 1' in prompts[0]
    assert "FALAS DE BASTIDOR DEVEM SER DESCARTADAS" in prompts[0]
    assert "REGRA DE RETAKE" in prompts[0]
    assert "Mantenha explicacoes, exemplos, repeticoes" in prompts[0]
    assert "O audio nao esta disponivel" in prompts[0]
    assert obter_diagnostico_editorial()["mode"] == "nvidia_text"


def test_nvidia_usa_parametros_kimi_e_endpoint_nvidia(monkeypatch) -> None:
    from editorial_ai import _nvidia_request

    monkeypatch.setattr("editorial_ai.NVIDIA_API_KEY", "chave-de-teste")
    chamadas = []
    def responder(method, url, payload, headers, timeout):
        chamadas.append((method, url, payload, headers, timeout))
        return {"choices": [{"message": {"content": '{"discard": []}'}}]}
    monkeypatch.setattr("editorial_ai._request_url", responder)
    assert _nvidia_request("teste") == '{"discard": []}'
    assert chamadas[0][1] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert chamadas[0][2]["stream"] is False
    assert chamadas[0][2]["model"] == "moonshotai/kimi-k3"
    assert chamadas[0][2]["max_tokens"] == 16384
    assert chamadas[0][2]["temperature"] == 0
    assert chamadas[0][3]["Authorization"] == "Bearer chave-de-teste"


def test_json_invalido_do_gemini_e_rejeitado_com_seguranca(monkeypatch):
    from editorial_ai import GeminiReviewError, _decidir_cortes_com_gemini

    monkeypatch.setattr(
        "editorial_ai._gemini_audio_request",
        lambda prompt, audio_path: '{"discard": [{"i": 0, "reason": "erro}',
    )
    resultado = _decidir_cortes_com_gemini([segmento(0, 1, "Teste")], "audio.wav")
    assert resultado.discard_indexes == set()


def test_resposta_parseada_do_sdk_e_preferida_ao_texto_invalido():
    from editorial_ai import _extrair_json_da_resposta_gemini

    class Resposta:
        text = '{"discard": [{"i": 0, "reason": "erro}'
        parsed = {"discard": [{"i": 0, "reason": "erro"}], "review": []}

    assert json.loads(_extrair_json_da_resposta_gemini(Resposta())) == {
        "discard": [{"i": 0, "reason": "erro"}], "review": [],
    }


def test_resposta_gemini_com_bloco_markdown_e_normalizada():
    from editorial_ai import _extrair_json_da_resposta_gemini

    class Resposta:
        text = '```json\n{"discard": [], "review": []}\n```'
        parsed = None

    assert _extrair_json_da_resposta_gemini(Resposta()) == '{"discard": [], "review": []}'


def test_prompt_multimodal_instrui_preservar_ultima_tentativa(monkeypatch):
    from editorial_ai import decidir_cortes_semanticos

    monkeypatch.setattr("editorial_ai.GEMINI_API_KEY", "chave-de-teste")
    prompt_recebido = []
    def responder(prompt, audio_path):
        prompt_recebido.append(prompt)
        return json.dumps({"discard": [{"i": 0, "reason": "falsa_partida"}]})
    monkeypatch.setattr("editorial_ai._gemini_audio_request", responder)
    resultado = decidir_cortes_semanticos(
        [segmento(0, 2, "Tentativa inicial."), segmento(3, 5, "Tentativa final.")],
        audio_path="aula.wav",
    )
    assert resultado.discard_indexes == {0}
    assert "mantenha sempre a ULTIMA tentativa completa" in prompt_recebido[0]
    assert "deixa eu ver um gancho aqui" in prompt_recebido[0]
