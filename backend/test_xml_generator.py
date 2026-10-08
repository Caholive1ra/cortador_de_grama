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


def test_plano_retido_preserva_outra_voz_no_master(tmp_path):
    fonte = dict(_fontes()[0], label="Participante 1", duration=5)
    audios = [
        dict(name="master", path="C:/master.wav", duration=5, kind="audio", enabled_by_default=True),
        dict(name="p1", path="C:/p1.wav", duration=5, kind="audio",
             camera_label="Participante 1", enabled_by_default=False),
    ]
    segmentos = [dict(start=0, end=3, enabled=True),
                 dict(start=3, end=5, enabled=True, audio_master_required=True)]
    path = tmp_path / "voz_preservada.xml"
    gerar_fcp_xml(segmentos, [fonte], str(path), 30, 5, fontes_audio=audios,
                  camera_por_segmento=["Participante 1"] * 2, audio_follows_camera=True)
    raiz = ET.parse(path).getroot()
    assert len(raiz.findall("./sequence/media/video/track/clipitem")) == 1
    assert [t.findtext("clipitem/enabled") for t in raiz.findall("./sequence/media/audio/track")] == ["TRUE", "FALSE"]


@pytest.mark.parametrize("seguir", [False, True])
def test_audio_segue_camera_com_vinculos_e_fallback(tmp_path, seguir):
    fontes = [dict(f, label=label, duration=6.0) for f, label in zip(
        _fontes(), ["PGM", "Participante 1", "Participante 2", "Câmera geral"])]
    audios = [
        dict(name="master.wav", path="C:/master.wav", duration=6.0,
             kind="audio", enabled_by_default=True),
        dict(name="p1.wav", path="C:/p1.wav", duration=6.0,
             kind="audio", enabled_by_default=False, camera_label="Participante 1"),
        dict(name="p2.wav", path="C:/p2.wav", duration=1.5, offset_seconds=1.0,
             kind="audio", enabled_by_default=False, camera_label="Participante 2"),
    ]
    segmentos = [dict(start=i, end=i+1, enabled=i != 5) for i in range(6)]
    cameras = ["Participante 1", "Participante 2", "Participante 2",
               "Câmera geral", "Participante 1", "Participante 1"]
    # Mantem separado o intervalo parcialmente coberto, sem alterar a compactacao.
    cameras[2] = "Participante 1"
    audios[1]["duration"] = 2.5
    destino = tmp_path / "audio_segue.xml"
    gerar_fcp_xml(segmentos, fontes, str(destino), 30, 6,
                  fontes_audio=audios, camera_por_segmento=cameras,
                  audio_follows_camera=seguir)
    raiz = ET.parse(destino).getroot()
    trilhas = raiz.findall("./sequence/media/audio/track")
    for frame, esperado in zip([0, 30, 60, 90, 120, 150], [1, 2, 0, 0, 0, None]):
        ativos = [i for i, trilha in enumerate(trilhas) for clip in trilha.findall("clipitem")
                  if int(clip.findtext("start")) <= frame < int(clip.findtext("end"))
                  and clip.findtext("enabled") == "TRUE"]
        assert ativos == ([] if esperado is None else [esperado if seguir else 0])
    clips = {clip.get("id"): clip for clip in raiz.findall(".//clipitem")}
    links = raiz.findall(".//link")
    assert bool(links) == seguir
    for clip in clips.values():
        refs = {link.findtext("linkclipref") for link in clip.findall("link")}
        for ref in refs:
            assert ref in clips
            assert {link.findtext("linkclipref") for link in clips[ref].findall("link")} == refs
    for link in links:
        tipo = link.findtext("mediatype")
        trilha = int(link.findtext("trackindex")) - 1
        indice = int(link.findtext("clipindex")) - 1
        referenciado = raiz.findall(f"./sequence/media/{tipo}/track")[trilha].findall("clipitem")[indice]
        assert referenciado.get("id") == link.findtext("linkclipref")


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


def test_une_microsegmentos_e_mantem_fronteiras_de_descarte(tmp_path) -> None:
    path = tmp_path / "cortes_reais.xml"
    fonte = _fontes()[:1]
    fonte[0]["duration"] = 5.0
    gerar_fcp_xml(
        [
            {"start": 0.0, "end": 1.0, "enabled": True},
            {"start": 1.0, "end": 2.0, "enabled": True},
            {"start": 2.0, "end": 3.0, "enabled": False},
            {"start": 3.0, "end": 4.0, "enabled": True},
            {"start": 4.0, "end": 5.0, "enabled": True},
        ],
        fonte, str(path), 30.0, 5.0,
    )
    raiz = ET.parse(path).getroot()
    clipes = raiz.findall("./sequence/media/video/track/clipitem")
    assert [
        (clipe.findtext("start"), clipe.findtext("end"), clipe.findtext("enabled"))
        for clipe in clipes
    ] == [("0", "60", "TRUE"), ("60", "90", "FALSE"), ("90", "150", "TRUE")]


def test_adiciona_marcador_para_trecho_com_duvida_da_ia(tmp_path) -> None:
    path = tmp_path / "revisar.xml"
    gerar_fcp_xml(
        [{"start": 0.0, "end": 3.0, "enabled": True, "review": True,
          "review_reason": "duvida_editorial"}],
        _fontes()[:1], str(path), 30.0, 3.0,
    )
    marcador = ET.parse(path).getroot().find("./sequence/marker")
    assert marcador is not None
    assert marcador.findtext("name") == "REVISAR: duvida da IA"
    assert (marcador.findtext("in"), marcador.findtext("out")) == ("0", "90")


def test_adiciona_marcador_de_lettering_sem_alterar_o_video(tmp_path) -> None:
    path = tmp_path / "lettering.xml"
    gerar_fcp_xml(
        [{"start": 0.0, "end": 3.0, "enabled": True}], _fontes()[:1], str(path),
        30.0, 3.0,
        lettering_suggestions=[{"start": 1.0, "end": 2.0, "text": "Conceito-chave", "reason": "conceito"}],
        sequence_name="Aula_Revisada_Sugestoes_Lettering",
    )
    raiz = ET.parse(path).getroot()
    assert raiz.findtext("./sequence/name") == "Aula_Revisada_Sugestoes_Lettering"
    marcador = raiz.find("./sequence/marker")
    assert marcador.findtext("name") == "LETTERING: Conceito-chave"
    assert marcador.findtext("comment") == "Por que: conceito"
    assert (marcador.findtext("in"), marcador.findtext("out")) == ("30", "60")


def test_gera_audio_externo_sincronizado(tmp_path) -> None:
    output_path = tmp_path / "audio_externo.xml"
    audio = {
        "name": "gravador.wav",
        "path": r"C:\Aulas\gravador.wav",
        "duration": 2.0,
        "offset_seconds": 1.0,
        "kind": "audio",
    }
    gerar_fcp_xml(
        [{"start": 0.0, "end": 3.0, "enabled": True}],
        _fontes()[:1],
        str(output_path),
        30.0,
        3.0,
        fonte_audio=audio,
    )
    raiz = ET.parse(output_path).getroot()
    clipe = raiz.find("./sequence/media/audio/track/clipitem")
    assert clipe is not None
    assert [clipe.findtext(chave) for chave in ("start", "end", "in", "out")] == [
        "30", "90", "0", "60"
    ]
    assert clipe.findtext("name") == "gravador.wav"
    assert clipe.find("file/pathurl") is not None


def test_gera_uma_trilha_para_cada_audio_externo(tmp_path) -> None:
    audios = [
        {"name": f"pessoa_{indice}.wav", "path": rf"C:\Aulas\pessoa_{indice}.wav",
         "duration": 3.0, "offset_seconds": 0.0, "kind": "audio"}
        for indice in range(1, 4)
    ]
    path = tmp_path / "videocast.xml"
    gerar_fcp_xml(
        [{"start": 0.0, "end": 3.0, "enabled": True}], _fontes(), str(path),
        30.0, 3.0, fontes_audio=audios,
    )
    raiz = ET.parse(path).getroot()
    trilhas = raiz.findall("./sequence/media/audio/track")
    assert len(trilhas) == 3
    assert [trilha.findtext("clipitem/name") for trilha in trilhas] == [
        "pessoa_1.wav", "pessoa_2.wav", "pessoa_3.wav"
    ]


def test_audio_alternativo_inicia_desabilitado_e_acompanha_corte(tmp_path) -> None:
    master = {"name": "master.wav", "path": r"C:\Aulas\master.wav", "duration": 2.0,
              "offset_seconds": 0.0, "kind": "audio", "enabled_by_default": True}
    alternativa = {"name": "camera.wav", "path": r"C:\Aulas\camera.wav", "duration": 2.0,
                   "offset_seconds": 0.0, "kind": "audio", "enabled_by_default": False}
    path = tmp_path / "audios_habilitados.xml"
    gerar_fcp_xml(
        [{"start": 0.0, "end": 1.0, "enabled": True},
         {"start": 1.0, "end": 2.0, "enabled": False}],
        _fontes()[:1], str(path), 30.0, 2.0, fontes_audio=[master, alternativa],
    )
    raiz = ET.parse(path).getroot()
    trilhas = raiz.findall("./sequence/media/audio/track")
    assert [clipe.findtext("enabled") for clipe in trilhas[0].findall("clipitem")] == ["TRUE", "FALSE"]
    assert [clipe.findtext("enabled") for clipe in trilhas[1].findall("clipitem")] == ["FALSE", "FALSE"]


def test_aceita_camera_5994_em_timeline_2997(tmp_path) -> None:
    fontes = _fontes()[:2]
    fontes[0]["fps"] = 29.97
    fontes[1]["fps"] = 59.94
    path = tmp_path / "fps_misto.xml"
    gerar_fcp_xml(
        [{"start": 0.0, "end": 2.0, "enabled": True}], fontes, str(path),
        29.97, 2.0,
    )
    raiz = ET.parse(path).getroot()
    camera_60p = raiz.findall("./sequence/media/video/track")[1].find("clipitem")
    assert camera_60p is not None
    assert [camera_60p.findtext(chave) for chave in ("start", "end", "in", "out")] == [
        "0", "60", "0", "120"
    ]


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
