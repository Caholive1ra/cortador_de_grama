import json

import pytest

from lettering_ai import LetteringModelError, sugerir_letterings


@pytest.fixture(autouse=True)
def mapa_mock(monkeypatch):
    import lettering_ai
    original = lettering_ai._solicitar_json_para_lettering
    monkeypatch.setattr(lettering_ai, "_solicitar_json_para_lettering",
                        lambda prompt: '{"mapa":"Conceitos centrais da aula."}'
                        if prompt.startswith("Mapeie") else original(prompt))


def test_sugestoes_usam_apenas_intervalos_da_transcricao(monkeypatch) -> None:
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr("lettering_ai._gemini_request", lambda prompt: json.dumps({
        "suggestions": [
            {"i": 1, "text": "Polimorfismo", "reason": "conceito"},
            {"i": 99, "text": "Inventado", "reason": "conceito"},
        ]
    }))
    resultado = sugerir_letterings([
        {"start": 0, "end": 3, "text": "Vamos começar."},
        {"start": 3, "end": 8, "text": "Polimorfismo permite comportamentos diferentes."},
    ])
    assert resultado[0]["start"] == 3.0
    assert resultado[0]["text"] == "Polimorfismo"
    assert resultado[0]["explanation"]


def test_sem_chave_nao_finge_ter_analise(monkeypatch) -> None:
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", None)
    monkeypatch.setattr("lettering_ai.NVIDIA_API_KEY", None)
    with pytest.raises(LetteringModelError, match="GEMINI_API_KEY"):
        sugerir_letterings([{"start": 0, "end": 1, "text": "Teste"}])


def test_modo_economico_cobre_tambem_o_final(monkeypatch) -> None:
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    prompts = []
    monkeypatch.setattr("lettering_ai._gemini_request", lambda prompt: prompts.append(prompt) or '{"suggestions": []}')
    segmentos = [
        {"start": indice, "end": indice + 1, "text": "Frase casual sem conceito."}
        for indice in range(80)
    ]
    segmentos[40]["text"] = "A definicao importante explica o conceito."
    sugerir_letterings(segmentos, modo_economico=True)
    assert '"i": 40' in prompts[0]
    assert any('"i": 79' in prompt for prompt in prompts)


def test_rejeita_highlight_acima_de_quatro_palavras(monkeypatch) -> None:
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    prompts = []
    monkeypatch.setattr("lettering_ai._gemini_request", lambda prompt: prompts.append(prompt) or json.dumps({
        "suggestions": [{
            "i": 0, "highlight": "Seguranca nao e um produto", "reason": "highlight",
        }]
    }))
    resultado = sugerir_letterings([{
        "start": 0, "end": 4, "text": "Seguranca nao e um produto, e um processo continuo.",
    }])
    assert resultado == []
    assert "1 a 4 palavras" in prompts[0]


def test_siglas_reaparecem_e_nao_sao_limitadas_a_doze(monkeypatch):
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr("lettering_ai._gemini_request", lambda _: '{"suggestions":[]}')
    resultado = sugerir_letterings([
        {"start": i * 4, "end": i * 4 + 2, "text": "Usamos API nesta etapa."}
        for i in range(20)
    ])
    assert len(resultado) == 20
    assert all(s["text"] == "API" and s["reason"] == "sigla" for s in resultado)


def test_nao_remove_negacao_para_formar_lettering(monkeypatch):
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr("lettering_ai._gemini_request", lambda _: json.dumps({
        "suggestions": [{"i": 0, "lettering": "Correlação implica causalidade", "reason": "regra"}]}))
    assert sugerir_letterings([{"start": 0, "end": 3, "text": "Correlação não implica causalidade."}]) == []


def test_siglas_soletradas_e_caixa_alta_nao_geram_palavras_falsas(monkeypatch):
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr("lettering_ai._gemini_request", lambda _: '{"suggestions":[]}')
    resultado = sugerir_letterings([
        {"start": 0, "end": 2, "text": "Utilize A P I aqui."},
        {"start": 3, "end": 5, "text": "ESTA FRASE ESTA EM MAIUSCULAS"},
    ])
    assert [s["text"] for s in resultado] == ["API"]


def test_resposta_invalida_nao_recorta_inicio_da_fala(monkeypatch):
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr("lettering_ai._gemini_request", lambda _: "sem JSON")
    assert sugerir_letterings([
        {"start": 0, "end": 3, "text": "Esta definição importante permite entender todo o conceito."}
    ]) == []


def test_provedor_indisponivel_ainda_entrega_siglas_detectadas(monkeypatch):
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr(
        "lettering_ai._solicitar_json_para_lettering",
        lambda _prompt: (_ for _ in ()).throw(LetteringModelError("503")),
    )
    resultado = sugerir_letterings([
        {"start": 0, "end": 3, "text": "A API recebe os dados."},
    ])
    assert resultado == [{
        "start": 0.0, "end": 3.0, "text": "API", "reason": "sigla",
        "evidence": "API",
        "explanation": "Sigla mencionada: confira o significado no material do curso.",
    }]


def test_lettering_tenta_gpt_oss_antes_do_gemma(monkeypatch):
    import lettering_ai as lettering
    import editorial_ai as ai

    ordem = []
    monkeypatch.setattr(lettering, "GEMINI_API_KEY", "gemini")
    monkeypatch.setattr(lettering, "NVIDIA_API_KEY", "nvidia")
    monkeypatch.setattr(lettering, "_gemini_request", lambda _prompt: (_ for _ in ()).throw(ai.GeminiReviewError("falhou")))
    monkeypatch.setattr(lettering, "_nvidia_request", lambda _prompt: ordem.append("gpt_oss") or (_ for _ in ()).throw(ai.NvidiaReviewError("falhou")))
    monkeypatch.setattr(lettering, "_gemma_request", lambda _prompt: ordem.append("gemma") or '{"suggestions": []}')

    assert lettering._solicitar_json_para_lettering("teste") == '{"suggestions": []}'
    assert ordem == ["gpt_oss", "gemma"]


def test_rejeita_highlight_inventado(monkeypatch) -> None:
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr("lettering_ai._gemini_request", lambda _prompt: json.dumps({
        "suggestions": [{"i": 0, "text": "Frase completamente inventada", "reason": "highlight"}]
    }))
    resultado = sugerir_letterings([{"start": 0, "end": 4, "text": "Conteudo tecnico da aula."}])
    assert resultado == []


def test_rejeita_regra_inventada_mesmo_sem_ser_highlight(monkeypatch) -> None:
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr("lettering_ai._gemini_request", lambda _prompt: json.dumps({
        "suggestions": [{"i": 0, "text": "Use interfaces sempre", "reason": "regra"}]
    }))
    resultado = sugerir_letterings([{
        "start": 0, "end": 4, "text": "Hoje vamos falar sobre polimorfismo.",
    }])
    assert resultado == []


def test_rejeita_rotulo_generico_e_duplicata(monkeypatch) -> None:
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", "teste")
    monkeypatch.setattr("lettering_ai._gemini_request", lambda _prompt: json.dumps({
        "suggestions": [
            {"i": 0, "text": "Importante", "reason": "alerta"},
            {"i": 1, "text": "Baixo acoplamento permite flexibilidade", "reason": "regra"},
            {"i": 2, "text": "Baixo acoplamento permite mais flexibilidade", "reason": "conclusao"},
        ]
    }))
    resultado = sugerir_letterings([
        {"start": 0, "end": 1, "text": "Isso e importante."},
        {"start": 1, "end": 3, "text": "Baixo acoplamento permite flexibilidade."},
        {"start": 3, "end": 5, "text": "Baixo acoplamento permite mais flexibilidade."},
    ])
    assert [item["text"] for item in resultado] == ["Baixo acoplamento permite flexibilidade"]
