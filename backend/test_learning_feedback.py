import json
import pytest

from learning_feedback import comparar_xmls, salvar_feedback, _normalizar_xmls


@pytest.fixture(autouse=True)
def banco_isolado(tmp_path, monkeypatch):
    monkeypatch.setattr("learning_feedback.DATA_DIR", tmp_path / "learning_data")


def _xml(intervalos, path):
    clips = "".join(
        f"<clipitem><enabled>{enabled}</enabled><start>{start}</start><end>{end}</end><in>{source_start}</in><out>{source_end}</out></clipitem>"
        for start, end, source_start, source_end, enabled in intervalos
    )
    path.write_text(
        f"<xmeml><sequence><duration>300</duration><rate><timebase>30</timebase></rate><media><video><track>{clips}</track></video></media></sequence></xmeml>",
        encoding="utf-8",
    )


def test_compara_area_mantida_e_cortes_do_editor(tmp_path):
    sugerido, revisado = tmp_path / "sugerido.xml", tmp_path / "revisado.xml"
    _xml([(0, 100, 0, 100, "TRUE"), (100, 200, 100, 200, "FALSE")], sugerido)
    # A sequencia final sofreu ripple, mas os tempos de origem sao 0-60 e 140-200.
    _xml([(0, 60, 0, 60, "TRUE"), (60, 120, 140, 200, "TRUE")], revisado)
    resultado = comparar_xmls(str(sugerido), str(revisado))
    assert resultado["accepted_kept"] == [[0, 60]]
    assert resultado["ai_kept_editor_cut"] == [[60, 100]]
    assert resultado["ai_cut_editor_kept"] == [[140, 200]]


def test_feedback_salva_apenas_segmentos_que_divergem(tmp_path, monkeypatch):
    sugerido, revisado = tmp_path / "aula_cortado_abc.xml", tmp_path / "final.xml"
    _xml([(0, 100, 0, 100, "TRUE")], sugerido)
    _xml([(0, 60, 0, 60, "TRUE")], revisado)
    diagnostico = tmp_path / "aula_diagnostico_abc.json"
    diagnostico.write_text(json.dumps({"segmentos": [
        {"start": 0, "end": 2, "text": "mantido", "reason": "fala_util"},
        {"start": 2, "end": 4, "text": "cortado", "reason": "fala_util"},
    ]}), encoding="utf-8")
    # O caminho explicito evita depender do diretorio de armazenamento neste contrato.
    resultado = comparar_xmls(str(sugerido), str(revisado))
    _, resumo = salvar_feedback(resultado, str(sugerido), str(revisado), diagnostic_path=str(diagnostico))
    assert resumo["diagnostic_found"] is True
    assert resumo["examples_saved"] == 1


def test_compara_dois_xmls_originais_unidos_na_mesma_aula(tmp_path):
    def com_fonte(path, nome, inicio, fim, duracao=100):
        path.write_text(
            f'''<xmeml><sequence><duration>{duracao}</duration><rate><timebase>30</timebase></rate><media><video><track>
            <clipitem><start>{inicio}</start><end>{fim}</end><in>0</in><out>{fim - inicio}</out><enabled>TRUE</enabled>
            <file id="f"><name>{nome}.mp4</name><pathurl>file:///C:/{nome}.mp4</pathurl><rate><timebase>30</timebase></rate></file></clipitem>
            </track></video></media></sequence></xmeml>''', encoding="utf-8"
        )

    primeiro, segundo, final = tmp_path / "01.xml", tmp_path / "1.1.xml", tmp_path / "final.xml"
    com_fonte(primeiro, "parte01", 0, 100)
    com_fonte(segundo, "parte11", 0, 100)
    final.write_text('''<xmeml><sequence><duration>200</duration><rate><timebase>30</timebase></rate><media><video><track>
        <clipitem><start>0</start><end>100</end><in>0</in><out>100</out><enabled>TRUE</enabled><file id="a"><name>parte01.mp4</name><pathurl>file:///C:/parte01.mp4</pathurl><rate><timebase>30</timebase></rate></file></clipitem>
        <clipitem><start>100</start><end>150</end><in>0</in><out>50</out><enabled>TRUE</enabled><file id="b"><name>parte11.mp4</name><pathurl>file:///C:/parte11.mp4</pathurl><rate><timebase>30</timebase></rate></file></clipitem>
        </track></video></media></sequence></xmeml>''', encoding="utf-8")

    resultado = comparar_xmls([str(primeiro), str(segundo)], str(final), project_type="videoaula")
    assert resultado["ai_kept_editor_cut"] == [[150, 200]]
    assert resultado["source_xmls"] == [str(primeiro), str(segundo)]


def test_ordena_partes_pgm_fragmentadas_pela_numeracao(tmp_path):
    primeiro = tmp_path / "aula_PGM_01_cortado_ab12.xml"
    segundo = tmp_path / "aula_PGM_1.1_cortado_cd34.xml"
    assert _normalizar_xmls([str(segundo), str(primeiro)]) == [str(primeiro), str(segundo)]
