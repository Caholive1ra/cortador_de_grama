"""Testes do gerador FCP XML multicâmera."""

import xml.etree.ElementTree as ET

import pytest

from xml_generator import gerar_fcp_xml, seconds_to_frames


def _fontes() -> list[dict]:
    nomes = ["PGM", "Câmera 1", "Câmera 2", "PPT/Tela"]
    return [
        {
            "name": nome,
            "path": rf"C:\Aulas\{nome}.mp4",
            "duration": 3.0,
            "fps": 30.0,
            "width": 1920,
            "height": 1080,
        }
        for nome in nomes
    ]


def test_seconds_to_frames() -> None:
    assert seconds_to_frames(1.5, 30.0) == 45
    assert seconds_to_frames(0.0, 30.0) == 0
    assert seconds_to_frames(1.0, 29.97) == 30


def test_gera_quatro_trilhas_com_segmentos_sincronizados(tmp_path) -> None:
    output_path = tmp_path / "aula_cortada.xml"
    segmentos = [
        {"start": 0.0, "end": 2.0, "enabled": True},
        {"start": 2.0, "end": 3.0, "enabled": False},
    ]

    gerar_fcp_xml(segmentos, _fontes(), str(output_path), 30.0, 3.0)

    raiz = ET.parse(output_path).getroot()
    sequencia = raiz.find("sequence")
    assert sequencia is not None
    assert sequencia.findtext("duration") == "90"
    trilhas = sequencia.findall("./media/video/track")
    assert len(trilhas) == 4
    assert all(len(trilha.findall("clipitem")) == 2 for trilha in trilhas)

    assinaturas = []
    for trilha in trilhas:
        assinaturas.append([
            (
                clipe.findtext("start"),
                clipe.findtext("end"),
                clipe.findtext("in"),
                clipe.findtext("out"),
                clipe.findtext("enabled"),
            )
            for clipe in trilha.findall("clipitem")
        ])
    assert all(assinatura == assinaturas[0] for assinatura in assinaturas)
    assert [item[4] for item in assinaturas[0]] == ["TRUE", "FALSE"]

    declaracoes = sequencia.findall(
        "./media/video/track/clipitem/file/pathurl"
    )
    assert len(declaracoes) == 4
    assert any("%20" in (item.text or "") for item in declaracoes)


def test_rejeita_timeline_com_gap(tmp_path) -> None:
    segmentos = [
        {"start": 0.0, "end": 1.0, "enabled": True},
        {"start": 2.0, "end": 3.0, "enabled": True},
    ]

    with pytest.raises(ValueError, match="contínua"):
        gerar_fcp_xml(
            segmentos, _fontes(), str(tmp_path / "invalido.xml"), 30.0, 3.0
        )


def test_gera_xml_somente_com_pgm(tmp_path) -> None:
    output_path = tmp_path / "somente_pgm.xml"
    gerar_fcp_xml(
        [{"start": 0.0, "end": 3.0, "enabled": True}],
        _fontes()[:1],
        str(output_path),
        30.0,
        3.0,
    )

    raiz = ET.parse(output_path).getroot()
    assert len(raiz.findall("./sequence/media/video/track")) == 1


def test_offsets_lacunas_limites_e_declaracao_tardia(tmp_path) -> None:
    fontes = _fontes()
    fontes[1].update(offset_seconds=2.0, duration=0.5)
    fontes[2].update(offset_seconds=-1.0, duration=3.0)
    fontes[3].update(offset_seconds=-2.0, duration=10.0)
    path = tmp_path / "offsets.xml"
    gerar_fcp_xml([
        {"start": 0.0, "end": 1.0, "enabled": True},
        {"start": 1.0, "end": 3.0, "enabled": False},
    ], fontes, str(path), 30, 3)
    root = ET.parse(path)
    tracks = root.findall("./sequence/media/video/track")
    late = tracks[1].find("clipitem")
    assert [late.findtext(key) for key in ("start", "end", "in", "out")] == ["60", "75", "0", "15"]
    assert late.find("file/pathurl") is not None
    assert late.findtext("enabled") == "FALSE"
    early = tracks[2].findall("clipitem")
    assert early[0].findtext("in") == "30"
    assert early[-1].findtext("end") == "60"
    for track, fonte in zip(tracks, fontes):
        for clip in track.findall("clipitem"):
            start, end, entry, out = [int(clip.findtext(k)) for k in ("start", "end", "in", "out")]
            assert 0 <= entry < out <= round(fonte["duration"] * 30)
            assert 0 <= start < end <= 90
            assert end - start == out - entry
    audio = root.findall("./sequence/media/audio/track/clipitem")
    assert [(clip.findtext("in"), clip.findtext("out")) for clip in audio] == [("0", "30"), ("30", "90")]
