"""Direção automática de videocast a partir de áudios individuais sincronizados."""

import logging
import wave

import numpy as np

logger = logging.getLogger(__name__)

LIMIAR_FALA = 0.35
MARGEM_DOMINANCIA = 0.15


def dirigir_cameras(
    segmentos: list[dict], fontes_por_camera: list[tuple[str, str, float]],
    camera_geral: str,
) -> list[str]:
    """Retorna a câmera sugerida para cada segmento.

    ``fontes_por_camera`` contém (rótulo da câmera, wav temporário, offset no
    PGM). Se duas fontes têm fala relevante, ou não há vencedor claro, usa a
    câmera geral. O método é propositalmente conservador: uma troca errada é
    pior que permanecer no plano geral.
    """
    if not fontes_por_camera:
        return [camera_geral] * len(segmentos)

    sinais = [(camera, *_ler_wav(path), offset) for camera, path, offset in fontes_por_camera]
    perfis = [_perfil_energia(sinal) for _camera, sinal, _rate, _offset in sinais]
    sugestoes: list[str] = []
    for segmento in segmentos:
        if not segmento.get("enabled", segmento.get("track") != "V1"):
            sugestoes.append(camera_geral)
            continue
        pontuacoes = []
        for (camera, sinal, rate, offset), perfil in zip(sinais, perfis):
            energia = _energia(sinal, rate, float(segmento["start"]) - offset,
                               float(segmento["end"]) - offset)
            pontuacoes.append((camera, _normalizar(energia, perfil)))
        pontuacoes.sort(key=lambda item: item[1], reverse=True)
        melhor_camera, melhor = pontuacoes[0]
        segundo = pontuacoes[1][1] if len(pontuacoes) > 1 else 0.0
        if melhor < LIMIAR_FALA or segundo >= LIMIAR_FALA or melhor - segundo < MARGEM_DOMINANCIA:
            sugestoes.append(camera_geral)
        else:
            sugestoes.append(melhor_camera)
    logger.info("Direção automática: %s", {
        camera: sugestoes.count(camera) for camera in sorted(set(sugestoes))
    })
    return sugestoes


def _ler_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as arquivo:
        rate = arquivo.getframerate()
        dados = np.frombuffer(arquivo.readframes(arquivo.getnframes()), dtype="<i2")
    return dados.astype(np.float64) / 32768.0, rate


def _perfil_energia(sinal: np.ndarray) -> tuple[float, float]:
    blocos = np.array_split(sinal, max(1, len(sinal) // 16000))
    valores = np.array([_energia_bloco(bloco) for bloco in blocos])
    base = float(np.percentile(valores, 20))
    topo = float(np.percentile(valores, 95))
    return base, max(topo - base, 1e-5)


def _energia(sinal: np.ndarray, rate: int, inicio: float, fim: float) -> float:
    primeiro = max(0, round(inicio * rate))
    ultimo = min(len(sinal), round(fim * rate))
    return _energia_bloco(sinal[primeiro:ultimo]) if ultimo > primeiro else 0.0


def _energia_bloco(bloco: np.ndarray) -> float:
    return float(np.sqrt(np.mean(bloco ** 2))) if len(bloco) else 0.0


def _normalizar(energia: float, perfil: tuple[float, float]) -> float:
    return max(0.0, (energia - perfil[0]) / perfil[1])
