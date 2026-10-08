import wave
import xml.etree.ElementTree as ET

import numpy as np

from camera_director import dirigir_cameras, _estabilizar_planos
from xml_generator import gerar_fcp_xml


def test_planos_no_xml_sem_flash_e_sem_sobra_curta(tmp_path):
    limites = [0, 5, 5.002, 10, 15, 19]
    segmentos = [dict(start=a, end=b, enabled=True) for a, b in zip(limites, limites[1:])]
    cameras = _estabilizar_planos(segmentos, ["P1", "P2", "P1", "P2", "P1"], "Geral")
    assert cameras == ["P1", "P2", "P2", "P2", "P2"]
    fontes = [dict(name=n, label=n, path=f"C:/{n}.mp4", duration=19,
                   fps=30, width=1920, height=1080) for n in ["P1", "P2", "Geral"]]
    path = tmp_path / "planos.xml"
    gerar_fcp_xml(segmentos, fontes, str(path), 30, 19, camera_por_segmento=cameras)
    ativos = [clip for clip in ET.parse(path).findall("./sequence/media/video/track/clipitem")
              if clip.findtext("enabled") == "TRUE"]
    assert len(ativos) == 2
    assert all(int(c.findtext("end")) - int(c.findtext("start")) >= 150 for c in ativos)


def test_planos_preservam_blocos_curtos_e_descartes():
    segmentos = [dict(start=0, end=2, enabled=True),
                 dict(start=2, end=3, enabled=False),
                 dict(start=3, end=8, enabled=True),
                 dict(start=8, end=13, enabled=True)]
    original = [dict(s) for s in segmentos]
    assert _estabilizar_planos(segmentos, ["P1", "P1", "P2", "P1"], "Geral") == [
        "P1", "Geral", "P2", "P1"]
    assert segmentos == original


def test_microsegmentos_mesmo_falante_mantem_camera():
    segmentos = [dict(start=i/10, end=(i+1)/10, enabled=True) for i in range(101)]
    assert _estabilizar_planos(segmentos, ["P1"] * 101, "Geral") == ["P1"] * 101


def test_troca_de_falante_aguarda_minimo_sem_forcar_geral():
    segmentos = [dict(start=i, end=i+1, enabled=True) for i in range(15)]
    detectadas = ["P1"] * 3 + ["P2"] * 5 + ["P1"] * 7
    assert _estabilizar_planos(segmentos, detectadas, "Geral") == (
        ["P1"] * 5 + ["P2"] * 5 + ["P1"] * 5
    )


def _wav(path, sinal):
    with wave.open(str(path), "wb") as arquivo:
        arquivo.setnchannels(1)
        arquivo.setsampwidth(2)
        arquivo.setframerate(16000)
        arquivo.writeframes((sinal * 20000).astype("<i2").tobytes())
    return str(path)


def test_dirige_para_falante_e_geral_em_sobreposicao(tmp_path):
    rate = 16000
    pessoa_1 = np.zeros(18 * rate)
    pessoa_2 = np.zeros(18 * rate)
    pessoa_1[:6 * rate] = 0.6
    pessoa_1[12 * rate:] = 0.6
    pessoa_2[6 * rate:] = 0.6
    segmentos = [
        {"start": 0, "end": 6, "enabled": True},
        {"start": 6, "end": 12, "enabled": True},
        {"start": 12, "end": 18, "enabled": True},
    ]
    resultado = dirigir_cameras(
        segmentos,
        [("Participante 1", _wav(tmp_path / "p1.wav", pessoa_1), 0.0),
         ("Participante 2", _wav(tmp_path / "p2.wav", pessoa_2), 0.0)],
        "Câmera geral",
    )
    assert resultado == ["Participante 1", "Participante 2", "Câmera geral"]
