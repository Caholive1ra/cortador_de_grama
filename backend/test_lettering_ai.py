import json

import pytest

from lettering_ai import LetteringModelError, sugerir_letterings


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
    with pytest.raises(LetteringModelError, match="GEMINI_API_KEY"):
        sugerir_letterings([{"start": 0, "end": 1, "text": "Teste"}])


def test_modo_economico_reduz_transcricao_enviada(monkeypatch) -> None:
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
    assert '"i": 79' not in prompts[0]
