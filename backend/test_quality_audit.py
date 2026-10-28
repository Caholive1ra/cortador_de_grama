import xml.etree.ElementTree as ET

from quality_audit import _auditar_cortes, _auditar_texto, _detectar_repeticoes_locais, _revisar_repeticoes_com_ia


def test_jump_cut_mesma_fonte_sem_scale_recebe_marcador_vermelho():
    timeline = {"fps": 30, "cuts": [5], "clips": [
        {"track": 0, "start": 0, "end": 5, "source": "a.mp4", "scale": 100},
        {"track": 0, "start": 5, "end": 10, "source": "a.mp4", "scale": 100},
    ]}
    finding = _auditar_cortes(timeline)[0]
    assert finding["category"] == "jump_cut"
    assert finding["color"] == "red"


def test_mudanca_de_scale_nao_e_jump_cut():
    timeline = {"fps": 30, "cuts": [], "clips": [
        {"track": 0, "start": 0, "end": 5, "source": "a.mp4", "scale": 100},
        {"track": 0, "start": 5, "end": 10, "source": "a.mp4", "scale": 112},
    ]}
    assert not _auditar_cortes(timeline)


def test_texto_indica_continuidade_repeticao_e_checagem_factual():
    findings = _auditar_texto([
        {"start": 0, "end": 2, "text": "Vamos explicar este conceito"},
        {"start": 2, "end": 4, "text": "e depois aplicar este conceito"},
        {"start": 4, "end": 6, "text": "Em 2024 usamos 30% do total."},
    ])
    assert {item["category"] for item in findings} >= {"continuidade", "conteudo"}


def test_repeticao_reformulada_em_varios_segmentos_vira_possivel_refacao():
    findings = _detectar_repeticoes_locais([
        {"start": 0, "end": 4, "text": "Para configurar o acesso criamos uma chave de seguranca para o ambiente"},
        {"start": 4, "end": 7, "text": "Essa chave fica protegida no projeto"},
        {"start": 7, "end": 9, "text": "Deixa eu explicar de novo"},
        {"start": 9, "end": 14, "text": "Para configurar o acesso voce cria uma chave de seguranca no ambiente"},
    ])
    assert findings
    assert any("REFACAO" in item["title"] for item in findings)
    assert findings[0]["evidence"]["antes"]


def test_auditoria_aceita_validacao_do_ollama_apos_falhas_remotas(monkeypatch):
    import editorial_ai as ai

    candidato = {
        "category": "repeticao", "start": 4, "end": 6,
        "title": "REPETICAO: fala semelhante", "description": "checar",
        "evidence": {"antes": "fala anterior", "depois": "fala repetida"},
    }
    monkeypatch.setattr("quality_audit._detectar_repeticoes_locais", lambda _segmentos: [candidato])
    monkeypatch.setattr(
        ai, "solicitar_json_textual_com_fallback_ollama",
        lambda _prompt: ('{"confirmar":[{"id":0,"tipo":"repeticao","motivo":"Fala repetida sem novo conteudo."}]}', "ollama"),
    )

    resultado = _revisar_repeticoes_com_ia([{"start": 0, "end": 2, "text": "texto"}])

    assert resultado["status"] == "completed_ollama"
    assert resultado["findings"][0]["title"] == "IA: REPETICAO POSSIVEL"
