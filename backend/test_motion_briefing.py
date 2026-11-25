import json
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from motion_briefing import gerar_briefing


def _pptx(path):
    with ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="p" xmlns:a="a"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Arquitetura RAG</a:t></a:r></a:p><a:p><a:r><a:t>Retriever documentos LLM</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')


def test_gera_briefing_local_com_timecode_e_evidencia(tmp_path):
    xml = tmp_path / "final.xml"
    xml.write_text('''<xmeml><sequence><rate><timebase>30</timebase></rate><media><video><track><clipitem><start>60</start><end>150</end><file id="f1"/></clipitem></track></video></media><file id="f1"><name>TELA_CAP02.mp4</name><pathurl>file:///Tela_CAP02.mp4</pathurl></file></sequence></xmeml>''')
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps([{"start": 2, "end": 4, "text": "O retriever busca documentos relevantes."}]))
    pptx = tmp_path / "aula.pptx"; _pptx(pptx)
    result = gerar_briefing(str(xml), str(transcript), str(pptx), "tela_cap02")
    assert result["events"] == 1
    briefing = (tmp_path / "final_briefing_motion.md").read_text()
    prompt = (tmp_path / "final_prompt_motion_ia.md").read_text()
    briefing_json = json.loads((tmp_path / "final_briefing_motion.json").read_text())
    assert "| Timecode | Slide candidato |" in briefing
    assert "00:02 -> 00:04" in briefing
    assert "Retriever" in (tmp_path / "final_timecodes_motion.csv").read_text(encoding="utf-8-sig")
    assert (tmp_path / "final_prompt_motion_ia.md").is_file()
    assert "Responda exclusivamente com uma unica tabela Markdown" in prompt
    assert "| Timecode | Slide | Contexto da fala | Instrucao de Arte/Motion | Nome do arquivo |" in prompt
    assert "03'46" in prompt
    assert "00'02_" in (tmp_path / "final_timecodes_motion.csv").read_text(encoding="utf-8-sig")
    event = briefing_json["events"][0]
    assert briefing_json["version"] == 2
    assert event["start"] == event["timeline_start_seconds"] == 2
    assert event["end"] == event["timeline_end_seconds"] == 4
    assert event["speech"] == "O retriever busca documentos relevantes."
    assert event["matched_elements"] == []
    assert event["production_status"] == "review"
    assert event["asset_filename"] is None


def test_rejeita_xml_com_entidade_externa(tmp_path):
    xml = tmp_path / "bad.xml"; xml.write_text('<!DOCTYPE x [<!ENTITY a SYSTEM "x">]><x/>')
    transcript = tmp_path / "t.json"; transcript.write_text('[]')
    pptx = tmp_path / "a.pptx"; _pptx(pptx)
    with pytest.raises(ValueError, match="entidades"):
        gerar_briefing(str(xml), str(transcript), str(pptx), "tela")


def test_aceita_doctype_simples_do_premiere(tmp_path):
    xml = tmp_path / "final.xml"
    xml.write_text('''<!DOCTYPE xmeml><xmeml><sequence><rate><timebase>30</timebase></rate><media><video><track><clipitem><start>60</start><end>150</end><file id="f1"/></clipitem></track></video></media><file id="f1"><name>TELA_CAP02.mp4</name></file></sequence></xmeml>''')
    transcript = tmp_path / "t.json"
    transcript.write_text(json.dumps([{"start": 2, "end": 4, "text": "O retriever busca documentos relevantes."}]))
    pptx = tmp_path / "a.pptx"; _pptx(pptx)

    result = gerar_briefing(str(xml), str(transcript), str(pptx), "tela")

    assert result["events"] == 1


def test_referencia_vazia_nao_apaga_nome_da_midia_de_tela(tmp_path):
    xml = tmp_path / "final.xml"
    xml.write_text('''<xmeml><sequence><rate><timebase>30</timebase></rate><media><video><track><clipitem><start>60</start><end>150</end><file id="f1"/></clipitem></track></video></media><file id="f1"><name>TELA_CAP02.mp4</name></file><file id="f1"/></sequence></xmeml>''')
    transcript = tmp_path / "t.json"
    transcript.write_text(json.dumps([{"start": 2, "end": 4, "text": "O retriever busca documentos relevantes."}]))
    pptx = tmp_path / "a.pptx"; _pptx(pptx)

    assert gerar_briefing(str(xml), str(transcript), str(pptx), "tela_cap02")["events"] == 1


def test_le_transcricao_com_duration_e_words(tmp_path):
    xml = tmp_path / "final.xml"
    xml.write_text('''<xmeml><sequence><rate><timebase>30</timebase></rate><media><video><track><clipitem><start>60</start><end>150</end><file id="f1"/></clipitem></track></video></media><file id="f1"><name>TELA_CAP02.mp4</name></file></sequence></xmeml>''')
    transcript = tmp_path / "words.json"
    transcript.write_text(json.dumps({"segments": [{"start": 2, "duration": 2, "words": [{"text": "Retriever", "start": 2, "duration": .5}, {"text": "documentos", "start": 2.5, "duration": .5}]}]}))
    pptx = tmp_path / "a.pptx"; _pptx(pptx)

    result = gerar_briefing(str(xml), str(transcript), str(pptx), "tela_cap02")

    assert result["events"] == 1
    assert "Retriever documentos" in (tmp_path / "final_briefing_motion.md").read_text()


def test_le_pdf_de_slides_localmente(tmp_path, monkeypatch):
    """PDFs usam pypdf no computador; nenhuma chamada HTTP participa disso."""
    class Page:
        def extract_text(self):
            return "Arquitetura RAG\nRetriever de documentos"

    class Reader:
        pages = [Page()]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=lambda _: Reader()))
    xml = tmp_path / "final.xml"
    xml.write_text('''<xmeml><sequence><rate><timebase>30</timebase></rate><media><video><track><clipitem><start>60</start><end>150</end><file id="f1"/></clipitem></track></video></media><file id="f1"><name>TELA_CAP02.mp4</name></file></sequence></xmeml>''')
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps([{"start": 2, "end": 4, "text": "O retriever busca documentos relevantes."}]))
    pdf = tmp_path / "aula.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% arquivo selecionado pelo usuario\n")

    result = gerar_briefing(str(xml), str(transcript), str(pdf), "tela_cap02")

    assert result["events"] == 1
    assert "Arquitetura RAG" in (tmp_path / "final_briefing_motion.md").read_text()
