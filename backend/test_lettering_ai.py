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
    assert resultado == [{
        "start": 3.0, "end": 8.0, "text": "Polimorfismo", "reason": "conceito",
    }]


def test_sem_chave_nao_finge_ter_analise(monkeypatch) -> None:
    monkeypatch.setattr("lettering_ai.GEMINI_API_KEY", None)
    with pytest.raises(LetteringModelError, match="GEMINI_API_KEY"):
        sugerir_letterings([{"start": 0, "end": 1, "text": "Teste"}])
