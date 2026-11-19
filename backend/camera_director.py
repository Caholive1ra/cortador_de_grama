"""Direção automática de videocast a partir de áudios individuais sincronizados."""

import logging
import re
import wave

import numpy as np

logger = logging.getLogger(__name__)

LIMIAR_FALA = 0.35
MARGEM_DOMINANCIA = 0.15
ENERGIA_HZ = 100
TEMPO_MINIMO_PLANO_SEGUNDOS = 5.0
TEMPO_MAXIMO_INTERJEICAO_SEGUNDOS = 2.8
TEMPO_MINIMO_INTERJEICAO_GERAL_SEGUNDOS = 0.5
PAUSA_NATURAL_MINIMA_SEGUNDOS = 0.18


def dirigir_cameras(
    segmentos: list[dict], fontes_por_camera: list[tuple[str, str, float]],
    camera_geral: str,
    cameras_detectadas: list[str] | None = None,
) -> list[str]:
    """Retorna a câmera sugerida para cada segmento.

    ``fontes_por_camera`` contém (rótulo da câmera, wav temporário, offset no
    PGM). Se duas fontes têm fala relevante, ou não há vencedor claro, usa a
    câmera geral. O método é propositalmente conservador: uma troca errada é
    pior que permanecer no plano geral.
    """
    if not fontes_por_camera:
        if cameras_detectadas is not None:
            cameras_detectadas.extend([camera_geral] * len(segmentos))
        return [camera_geral] * len(segmentos)

    # Mantemos apenas um envelope RMS de 100 Hz. Isso evita reter WAVs de
    # varias horas como arrays float64 na memoria durante um videocast.
    sinais = [(camera, *_ler_envelope(path), offset) for camera, path, offset in fontes_por_camera]
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
    if cameras_detectadas is not None:
        cameras_detectadas.extend(sugestoes)
    sugestoes = _estabilizar_planos(segmentos, sugestoes, camera_geral)
    logger.info("Direção automática: %s", {
        camera: sugestoes.count(camera) for camera in sorted(set(sugestoes))
    })
    return sugestoes


def _estabilizar_planos(segmentos: list[dict], cameras: list[str], geral: str) -> list[str]:
    """Cria cadencia editorial em vez de colar a camera a cada voz detectada."""
    resultado = [geral] * len(segmentos)
    bloco: list[int] = []

    def concluir() -> None:
        if not bloco:
            return
        sugestoes = _substituir_interjeicoes_por_geral(
            segmentos, [cameras[i] for i in bloco], bloco, geral,
        )
        camera = sugestoes[0]
        inicio_plano = float(segmentos[bloco[0]]["start"])
        fim_bloco = float(segmentos[bloco[-1]]["end"])
        for posicao, i in enumerate(bloco):
            inicio = float(segmentos[i]["start"])
            sugestao = sugestoes[posicao]
            if sugestao != camera:
                plano_tem_tempo = inicio - inicio_plano >= TEMPO_MINIMO_PLANO_SEGUNDOS - 1e-9
                if sugestao == geral:
                    # Em sobreposicao ou comentario rapido, a geral e sempre
                    # mais segura do que apontar para quem interrompeu.
                    camera = geral
                    inicio_plano = inicio
                elif ((plano_tem_tempo or (
                        camera == geral and inicio - inicio_plano <= TEMPO_MAXIMO_INTERJEICAO_SEGUNDOS
                    )) and fim_bloco - inicio >= TEMPO_MINIMO_PLANO_SEGUNDOS - 1e-9
                      and _ponto_natural_de_troca(segmentos, i, bloco, posicao)):
                    camera = sugestao
                    inicio_plano = inicio
            resultado[i] = camera

    for i, segmento in enumerate(segmentos):
        habilitado = segmento.get("enabled", segmento.get("track") != "V1")
        if bloco and (not habilitado or abs(float(segmento["start"]) - float(segmentos[bloco[-1]]["end"])) > 0.001):
            concluir()
            bloco = []
        if habilitado:
            bloco.append(i)
    concluir()
    return resultado


def _ponto_natural_de_troca(segmentos: list[dict], indice: int, bloco: list[int], posicao: int) -> bool:
    """Permite trocar entre falas em respiro ou no fechamento de frase."""
    if posicao == 0:
        return True
    anterior = segmentos[bloco[posicao - 1]]
    atual = segmentos[indice]
    if float(atual["start"]) - float(anterior["end"]) >= PAUSA_NATURAL_MINIMA_SEGUNDOS:
        return True
    texto = str(anterior.get("text", "")).strip()
    # Sem transcricao, a fronteira do XML ja e o melhor ponto disponivel.
    return not texto or bool(re.search(r'[.!?]["”\')\]]*$', texto))


def _substituir_interjeicoes_por_geral(
    segmentos: list[dict], cameras: list[str], bloco: list[int], geral: str,
) -> list[str]:
    """Converte intervencao curta entre falas no plano geral."""
    resultado = list(cameras)
    inicio = 1
    while inicio < len(resultado) - 1:
        camera = resultado[inicio]
        fim = inicio + 1
        while fim < len(resultado) and resultado[fim] == camera:
            fim += 1
        anterior = resultado[inicio - 1]
        proxima = resultado[fim] if fim < len(resultado) else None
        duracao = float(segmentos[bloco[fim - 1]]["end"]) - float(segmentos[bloco[inicio]]["start"])
        if (camera != geral and anterior != geral and camera != anterior and proxima == anterior
                and duracao <= TEMPO_MAXIMO_INTERJEICAO_SEGUNDOS):
            # Um ruído de poucos frames não deve criar flash no plano geral.
            substituta = geral if duracao >= TEMPO_MINIMO_INTERJEICAO_GERAL_SEGUNDOS else anterior
            resultado[inicio:fim] = [substituta] * (fim - inicio)
        inicio = fim
    return resultado


def _ler_envelope(path: str) -> tuple[np.ndarray, int]:
    """Le um WAV PCM16 mono em blocos e devolve RMS a 100 Hz."""
    partes = []
    with wave.open(path, "rb") as arquivo:
        rate = arquivo.getframerate()
        if arquivo.getnchannels() != 1 or arquivo.getsampwidth() != 2 or rate % ENERGIA_HZ:
            raise ValueError("Direcao de cameras exige WAV mono PCM16 com taxa multipla de 100 Hz.")
        bloco = rate // ENERGIA_HZ
        while raw := arquivo.readframes(bloco * 6000):
            dados = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
            dados = dados[:len(dados) // bloco * bloco]
            if len(dados):
                partes.append(np.sqrt(np.mean(dados.reshape(-1, bloco) ** 2, axis=1)))
    return (np.concatenate(partes) if partes else np.empty(0)), ENERGIA_HZ


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
