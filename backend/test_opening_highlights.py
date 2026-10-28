import xml.etree.ElementTree as ET
import pytest

from opening_highlights import selecionar_abertura, criar_candidatos, validar_trechos
from xml_generator import gerar_fcp_xml_abertura


def _segmento(inicio, texto):
    return {"start": inicio, "end": inicio + 9, "text": texto}


def test_junta_frase_dividida_pelo_whisper_e_descarta_final_incompleto():
    candidatos = criar_candidatos([
        {"start": 0, "end": 3, "text": "O maior erro foi acreditar que"},
        {"start": 3, "end": 8, "text": "o dinheiro resolveria todos os problemas da empresa."},
        {"start": 8, "end": 12, "text": "Mas quando nós"},
    ])
    assert len(candidatos) == 1
    assert candidatos[0]["text"].startswith("O maior erro")
    assert candidatos[0]["end"] == 8
    assert "Mas quando" not in candidatos[0]["text"]


def test_bloqueia_ids_diferentes_com_audio_sobreposto():
    with pytest.raises(ValueError, match="sobrepostos"):
        validar_trechos([
            {"start": 10, "end": 20, "text": "Uma pergunta sobre os riscos."},
            {"start": 15, "end": 25, "text": "Uma resposta sobre as oportunidades."},
        ])


def test_bloqueia_mesma_fala_gravada_em_outro_momento():
    with pytest.raises(ValueError, match="repetidas"):
        validar_trechos([
            {"start": 10, "end": 20, "text": "O preço da escolha foi perder toda a equipe."},
            {"start": 100, "end": 110, "text": "O preço da escolha foi perder toda a equipe!"},
        ])


def test_nao_apara_pausa_interna_se_a_fala_retoma_antes_do_fim(monkeypatch):
    from types import SimpleNamespace
    from media_utils import aparar_silencios_das_bordas
    monkeypatch.setattr("media_utils.subprocess.run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stderr="silence_start: 9.6\nsilence_end: 9.8"))
    trecho = {"start": 0, "end": 10, "duration": 10, "text": "Fala."}
    assert aparar_silencios_das_bordas("teste.wav", [trecho])[0]["end"] == 10


def test_selecao_cria_abertura_cronologica_com_falas_de_todo_videocast():
    segmentos = [
        _segmento(0, "Hoje vamos conversar sobre um problema importante que mudou todo o mercado."),
        _segmento(500, "O ponto é que essa decisão cria uma oportunidade real para quem entende o risco."),
        _segmento(1100, "E no futuro ninguém vai poder ignorar essa diferença essencial."),
        _segmento(1500, "A pergunta é como transformar esse risco em um resultado melhor na prática."),
    ]
    resultado = selecionar_abertura(
        segmentos, lambda _prompt: '{"selection":[{"id":0},{"id":1},{"id":2},{"id":3}]}'
    )
    assert len(resultado) == 4
    assert [item["start"] for item in resultado] == sorted(item["start"] for item in resultado)
    assert 30 <= sum(item["duration"] for item in resultado) <= 40


def test_rejeita_curadoria_que_nao_respeita_duracao():
    segmentos = [
        _segmento(0, "Uma verdade importante muda o futuro e cria uma oportunidade real para todos."),
        _segmento(100, "O problema é que essa decisão traz risco e exige uma escolha muito importante."),
        _segmento(200, "A diferença essencial é entender o resultado antes de tomar uma decisão crítica."),
    ]
    try:
        selecionar_abertura(segmentos, lambda _prompt: '{"selection":[{"id":0}]}')
    except ValueError as exc:
        assert "não conseguiu" in str(exc)
    else:
        raise AssertionError("Uma seleção curta deveria ser rejeitada.")


def test_xml_abertura_condensa_os_trechos_na_timeline(tmp_path):
    fonte = {"name": "videocast.mp4", "path": str(tmp_path / "videocast.mp4"), "duration": 100,
             "fps": 30, "width": 1920, "height": 1080}
    destino = tmp_path / "abertura.xml"
    gerar_fcp_xml_abertura([
        {"start": 20, "end": 25, "text": "Primeira fala"},
        {"start": 70, "end": 78, "text": "Segunda fala"},
    ], fonte, str(destino), 30)
    raiz = ET.parse(destino).getroot()
    clipes = raiz.findall("./sequence/media/video/track/clipitem")
    assert [(c.findtext("start"), c.findtext("end"), c.findtext("in"), c.findtext("out")) for c in clipes] == [
        ("0", "150", "600", "750"), ("150", "390", "2100", "2340"),
    ]
