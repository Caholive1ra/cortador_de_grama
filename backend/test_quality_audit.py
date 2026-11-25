import xml.etree.ElementTree as ET
import json
import subprocess
import shutil
import pytest
import numpy as np

from quality_audit import _auditar_cortes, _auditar_texto, _detectar_repeticoes_locais, _revisar_repeticoes_com_ia
from quality_audit import _ler_eventos_render, _auditar_render_completo, _detectar_saturacao, auditar_sequencia


def _repetidas():
    return [
        {"start": 0, "end": 5, "text": "A configuração da aplicação depende da permissão de acesso ao ambiente seguro."},
        {"start": 600, "end": 605, "text": "A configuracao da aplicacao depende da permissao de acesso ao ambiente seguro."},
    ]


def test_repeticao_distante_com_acentos_tem_dois_tempos_e_um_alerta():
    achados = _detectar_repeticoes_locais(_repetidas())
    assert len(achados) == 1
    assert achados[0]["start"] == 600
    assert achados[0]["evidence"]["before_start"] == 0
    assert achados[0]["evidence"]["before_end"] == 5


def test_vocabulario_do_mesmo_tema_nao_basta_para_repeticao():
    assert not _detectar_repeticoes_locais([
        {"start": 0, "end": 5, "text": "A segurança da aplicação exige configurar as permissões do ambiente."},
        {"start": 5, "end": 10, "text": "Já o ambiente de desenvolvimento permite testar versões diferentes antes da publicação."},
    ])


def test_repeticoes_sem_sobreposicao_de_alertas():
    itens = _repetidas()
    itens.insert(1, {"start": 5, "end": 8, "text": "Vamos definir os parâmetros necessários."})
    itens.append({"start": 605, "end": 608, "text": "Vamos definir os parâmetros necessários."})
    achados = _detectar_repeticoes_locais(itens)
    assert achados
    assert all(a["end"] <= b["start"] for a, b in zip(achados, achados[1:]))


def test_ia_revisa_candidatos_apos_os_primeiros_doze(monkeypatch):
    candidatos = [dict(start=i * 10, end=i * 10 + 5, description="Comparar.", evidence={}) for i in range(25)]
    chamadas = []
    def solicitar(prompt):
        contexto = json.loads(prompt.split("\n", 1)[1])
        chamadas.append(contexto)
        return json.dumps({"confirmar": [{"id": c["id"], "tipo": "didatica", "motivo": "Resumo intencional."}
                                        for c in contexto]}), "ollama"
    monkeypatch.setattr("editorial_ai.solicitar_json_textual_com_fallback_ollama", solicitar)
    resultado = _revisar_repeticoes_com_ia([], candidatos)
    assert len(chamadas) == 3
    assert resultado["reviewed_ids"] == list(range(25))
    assert resultado["findings"] == []


def test_ia_com_id_inventado_preserva_alerta_local(monkeypatch):
    monkeypatch.setattr("editorial_ai.solicitar_json_textual_com_fallback_ollama",
        lambda prompt: ('{"confirmar":[{"id":99,"tipo":"didatica","motivo":"Resumo."}]}', "remote"))
    resultado = _revisar_repeticoes_com_ia(_repetidas())
    assert resultado["reviewed_ids"] == []
    assert resultado["status"].startswith("partial_")


def test_ia_nao_duplica_alerta_e_retira_repeticao_didatica(monkeypatch, tmp_path):
    xml, media = tmp_path / "a.xml", tmp_path / "a.mp4"
    xml.write_text("<xmeml><sequence><rate><timebase>30</timebase></rate></sequence></xmeml>")
    media.write_bytes(b"mock")
    monkeypatch.setattr("quality_audit._auditar_audio", lambda *a: [])
    monkeypatch.setattr("quality_audit._auditar_imagem", lambda *a: [])
    monkeypatch.setattr("quality_audit._auditar_render_completo", lambda *a: {"status": "completed", "findings": []})
    monkeypatch.setattr("editorial_ai.solicitar_json_textual_com_fallback_ollama",
        lambda prompt: ('{"confirmar":[{"id":0,"tipo":"didatica","motivo":"Reforço didático."}]}', "ollama"))
    resultado = auditar_sequencia(str(xml), str(media), _repetidas())
    assert not [f for f in resultado["findings"] if f["category"] == "repeticao"]
    assert resultado["checked"]["repetition_reviewed"] == 1
    assert resultado["checked"]["repetition_pending"] == 0


def test_varredura_le_eventos_fora_das_emendas():
    achados = _ler_eventos_render('''black_start:12 black_end:13 black_duration:1
silence_start: 20
silence_end: 24 | silence_duration: 4
lavfi.freezedetect.freeze_start: 30
lavfi.freezedetect.freeze_end: 35
''')
    assert [(a["start"], a["end"]) for a in achados] == [(12, 13), (20, 24), (30, 35)]
    assert all(a["requires_review"] for a in achados)


def test_timeout_da_varredura_nao_significa_render_sem_erros(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 10)
    monkeypatch.setattr("quality_audit.subprocess.run", timeout)
    assert _auditar_render_completo("a.mp4")["status"] == "unavailable"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg não instalado")
def test_varredura_real_de_render_preto_e_silencioso(tmp_path):
    media = tmp_path / "preto.mkv"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=160x90:r=10:d=4", "-f", "lavfi", "-i",
                    "anullsrc=r=16000:cl=mono", "-t", "4", "-c:v", "ffv1", "-c:a", "pcm_s16le",
                    str(media)], check=True, capture_output=True, timeout=30)
    resultado = _auditar_render_completo(str(media))
    assert resultado["status"] == "completed"
    assert {a["category"] for a in resultado["findings"]} == {"audio", "imagem"}
    assert any("preto" in a["title"] for a in resultado["findings"])


def test_saturacao_detectada_longe_de_cortes_e_agrupada():
    sinal = np.zeros(5000)
    sinal[2100:2200] = 1
    sinal[3100:3200] = -1
    achados = _detectar_saturacao(sinal, 1000)
    assert [(a["start"], a["end"]) for a in achados] == [(2, 4)]
    assert not _detectar_saturacao(np.ones(2000) * .5, 1000)


def test_divisao_continua_da_mesma_midia_nao_gera_jump_cut():
    assert not _auditar_cortes({"fps": 30, "clips": [
        {"track": 0, "start": 0, "end": 5, "source": "a", "scale": 100, "source_out": 5},
        {"track": 0, "start": 5, "end": 10, "source": "a", "scale": 100, "source_in": 5},
    ]})


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


def test_direcao_mba_retorna_marcador_sem_aplicar_efeito(monkeypatch):
    import mba_direction

    monkeypatch.setattr(mba_direction, "_frame_jpeg", lambda *_args: None)
    monkeypatch.setattr(mba_direction, "_solicitar_visao_gemini", lambda candidatos, _frames: {
        "suggestions": [{"id": candidatos[0]["id"], "type": "zoom_in", "strength": 2,
                         "duration": 2, "reason": "Definição central para a tomada de decisão."}]
    })
    resultado = mba_direction.sugerir_direcao_mba("aula.mp4", [{
        "start": 10, "end": 14, "text": "Este é o conceito mais importante desta disciplina MBA.",
    }])
    assert resultado["status"] == "gemini_vision"
    assert resultado["findings"][0]["category"] == "direcao"
    assert "ZOOM-IN" in resultado["findings"][0]["title"]
    assert "Escala sugerida" in resultado["findings"][0]["description"]


def test_auditoria_entrega_parcial_quando_audio_e_imagem_falham(monkeypatch, tmp_path):
    xml, media = tmp_path / "a.xml", tmp_path / "a.mp4"
    xml.write_text("<xmeml><sequence><rate><timebase>30</timebase></rate></sequence></xmeml>")
    media.write_bytes(b"mock")
    monkeypatch.setattr("quality_audit._auditar_audio", lambda *a: (_ for _ in ()).throw(RuntimeError("audio")))
    monkeypatch.setattr("quality_audit._auditar_imagem", lambda *a: (_ for _ in ()).throw(RuntimeError("imagem")))
    monkeypatch.setattr("quality_audit._auditar_render_completo", lambda *a: {"status": "completed", "findings": []})
    monkeypatch.setattr("quality_audit._revisar_repeticoes_com_ia", lambda *a: {"status": "not_needed", "findings": [], "reviewed_ids": []})

    resultado = auditar_sequencia(str(xml), str(media), [])

    assert resultado["checked"]["partial_checks"] == ["audio", "imagem"]
    assert len(resultado["findings"]) >= 2
