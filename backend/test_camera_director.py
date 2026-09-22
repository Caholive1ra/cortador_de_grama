import wave

import numpy as np

from camera_director import dirigir_cameras


def _wav(path, sinal):
    with wave.open(str(path), "wb") as arquivo:
        arquivo.setnchannels(1)
        arquivo.setsampwidth(2)
        arquivo.setframerate(16000)
        arquivo.writeframes((sinal * 20000).astype("<i2").tobytes())
    return str(path)


def test_dirige_para_falante_e_geral_em_sobreposicao(tmp_path):
    rate = 16000
    pessoa_1 = np.zeros(6 * rate)
    pessoa_2 = np.zeros(6 * rate)
    pessoa_1[:2 * rate] = 0.6
    pessoa_1[4 * rate:] = 0.6
    pessoa_2[2 * rate:] = 0.6
    segmentos = [
        {"start": 0, "end": 2, "enabled": True},
        {"start": 2, "end": 4, "enabled": True},
        {"start": 4, "end": 6, "enabled": True},
    ]
    resultado = dirigir_cameras(
        segmentos,
        [("Participante 1", _wav(tmp_path / "p1.wav", pessoa_1), 0.0),
         ("Participante 2", _wav(tmp_path / "p2.wav", pessoa_2), 0.0)],
        "Câmera geral",
    )
    assert resultado == ["Participante 1", "Participante 2", "Câmera geral"]
