import json
from pathlib import Path

import pytest

import learning_feedback as feedback
import learning_bank
from learning_bank import anotar, avaliar, definir_papel, ler_registro, listar_banco


@pytest.fixture(autouse=True)
def isolated_bank(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "DATA_DIR", tmp_path / "bank")
    monkeypatch.setattr(learning_bank, "_teste", lambda _: False)


def xml(path, intervals, source="file:///C:/media/aula.mp4", ntsc=False, graphic=False):
    clips = "".join(
        f'<clipitem><start>{a}</start><end>{b}</end><in>{a}</in><out>{b}</out><enabled>{enabled}</enabled>'
        f'<file id="f"><name>aula.mp4</name><pathurl>{source}</pathurl><rate><timebase>30</timebase><ntsc>{str(ntsc).upper()}</ntsc></rate></file></clipitem>'
        for a, b, enabled in intervals
    )
    if graphic:
        clips += '<clipitem><start>0</start><end>90</end><in>0</in><out>90</out><file id="g"><name>logo.png</name><pathurl>file:///C:/logo.png</pathurl></file></clipitem>'
    path.write_text(f'<xmeml><sequence><duration>180</duration><rate><timebase>30</timebase><ntsc>{str(ntsc).upper()}</ntsc></rate><media><video><track>{clips}</track></video></media></sequence></xmeml>', encoding="utf-8")


def record(tmp_path, final_intervals=None):
    original, final = tmp_path / "aula_cortado_a.xml", tmp_path / "final.xml"
    xml(original, [(0, 60, "TRUE"), (60, 90, "FALSE")])
    xml(final, final_intervals or [(0, 30, "TRUE"), (60, 90, "TRUE")])
    (tmp_path / "aula_diagnostico_a.json").write_text(json.dumps({"segmentos": [
        {"start": 0, "end": 1, "text": "Contexto anterior"},
        {"start": 1, "end": 2, "text": "Tentativa antiga"},
        {"start": 2, "end": 3, "text": "Tomada que deve ficar"},
    ]}), encoding="utf-8")
    comparison = feedback.comparar_xmls(str(original), str(final))
    _, summary = feedback.salvar_feedback(comparison, str(original), str(final), "videocast")
    return summary["feedback_id"], original, final


def test_contexto_idempotencia_preserva_anotacoes(tmp_path):
    rid, original, final = record(tmp_path)
    r = ler_registro(rid)
    assert r["eligible"]
    assert r["examples"][0]["context_before"][0]["text"] == "Contexto anterior"
    assert r["examples"][0]["context_after"][0]["text"] == "Tomada que deve ficar"
    anotar(rid, ["0"], "approved", "retake", "Primeira tomada substituida")
    _, summary = feedback.salvar_feedback(feedback.comparar_xmls(str(original), str(final)), str(original), str(final), "videocast")
    assert summary["duplicate"]
    assert summary["feedback_id"] == rid
    assert ler_registro(rid)["examples"][0]["status"] == "approved"
    assert len(list(feedback.DATA_DIR.glob("feedback_*.json"))) == 1


def test_rejeita_fonte_diferente_com_mesmo_nome(tmp_path):
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    xml(a, [(0, 30, "TRUE")])
    xml(b, [(0, 30, "TRUE")], source="file:///C:/outra/aula.mp4")
    with pytest.raises(ValueError, match="fontes"):
        feedback.comparar_xmls(str(a), str(b))


def test_logo_nao_vira_fala_mantida(tmp_path):
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    xml(a, [(0, 90, "TRUE")])
    xml(b, [(0, 30, "TRUE")], graphic=True)
    comparison = feedback.comparar_xmls(str(a), str(b))
    assert comparison["ai_kept_editor_cut"] == [[30, 90]]
    assert comparison["validated_sources"]
    assert comparison["ignored_graphics"] == ["file:///c:/logo.png"]


def test_ntsc_mede_tempo_real(tmp_path):
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    xml(a, [(0, 60, "TRUE")], ntsc=True)
    xml(b, [(0, 30, "TRUE")], ntsc=True)
    c = feedback.comparar_xmls(str(a), str(b))
    assert c["ai_kept_editor_cut"] == [[30, 60]]
    assert c["metrics"]["unwanted_kept_seconds"] == 1.001


def test_diagnostico_de_outro_projeto_rejeitado(tmp_path):
    with pytest.raises(ValueError, match="diagnostico"):
        feedback._carregar_diagnostico(str(tmp_path / "a_cortado_123.xml"), str(tmp_path / "b_diagnostico_123.json"))


def test_registro_teste_excluido_da_listagem(tmp_path, monkeypatch):
    rid, _, _ = record(tmp_path)
    path = feedback.DATA_DIR / f"feedback_{rid}.json"
    r = json.loads(path.read_text(encoding="utf-8"))
    r["suggested_xml"] = "C:/pytest-test/a.xml"
    path.write_text(json.dumps(r), encoding="utf-8")
    monkeypatch.setattr(learning_bank, "_teste", learning_bank._teste.__wrapped__ if hasattr(learning_bank._teste, "__wrapped__") else lambda value: "pytest" in str(value.get("suggested_xml", "")).lower())
    assert listar_banco()["excluded_test_records"] == 1
    assert listar_banco()["records"] == []
    with pytest.raises(ValueError):
        anotar(rid, ["0"], "approved", "retake")


def test_anotacoes_validam_ids_motivos_e_sao_reversiveis(tmp_path):
    rid, _, _ = record(tmp_path)
    with pytest.raises(ValueError):
        anotar(rid, ["0"], "approved", None)
    with pytest.raises(ValueError):
        anotar(rid, ["999"], "approved", "retake")
    anotar(rid, ["0", "1"], "approved", "duracao")
    anotar(rid, ["0"], "pending", None)
    r = anotar(rid, ["1"], "excluded", None)
    assert [e["status"] for e in r["examples"]] == ["pending", "excluded"]
    with pytest.raises(ValueError):
        ler_registro("../fora")


def test_benchmark_compara_antes_depois_sem_aplicar(tmp_path):
    rid, original, final = record(tmp_path)
    with pytest.raises(ValueError, match="reservado"):
        avaliar(rid, str(final))
    definir_papel(rid, "benchmark")
    resultado = avaliar(rid, str(final))
    assert resultado["candidate"]["useful_removed_seconds"] == 0
    assert resultado["delta_seconds"]["useful_removed_seconds"] == -1
    assert resultado["delta_seconds"]["unwanted_kept_seconds"] == -1
    assert avaliar(rid, str(original))["delta_seconds"]["useful_removed_seconds"] == 0


def test_mesmo_material_nao_pode_ser_referencia_e_benchmark(tmp_path):
    rid, original, _ = record(tmp_path)
    new_final = tmp_path / "outra_versao.xml"
    xml(new_final, [(0, 30, "TRUE")])
    _, summary = feedback.salvar_feedback(feedback.comparar_xmls(str(original), str(new_final)), str(original), str(new_final), "videocast")
    with pytest.raises(ValueError, match="mesmo material"):
        definir_papel(rid, "benchmark")
    definir_papel(summary["feedback_id"], "archived")
    assert definir_papel(rid, "benchmark")["dataset_role"] == "benchmark"


def test_xml_invalido_e_sequencias_aninhadas(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("<xmeml>", encoding="utf-8")
    with pytest.raises(ValueError, match="invalido"):
        feedback._ler_timeline(str(p))
    p.write_text("<xmeml><sequence><sequence/></sequence></xmeml>", encoding="utf-8")
    with pytest.raises(ValueError, match="unica"):
        feedback._ler_timeline(str(p))
