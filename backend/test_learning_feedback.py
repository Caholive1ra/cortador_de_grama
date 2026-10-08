import json
import pytest

from learning_feedback import comparar_xmls, salvar_feedback


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
