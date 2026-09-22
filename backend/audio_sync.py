"""Alinhamento conservador por audio, sem modificar ou acelerar os brutos.

offset_seconds e a posicao do inicio da fonte na timeline PGM:
tempo_fonte = tempo_pgm - offset_seconds. Positivo indica inicio tardio.
"""

import logging
import wave
from dataclasses import asdict, dataclass

import numpy as np

logger = logging.getLogger(__name__)
ENVELOPE_HZ = 100
MAXIMO_DESVIO_SINCRONIZACAO_QUADROS = 1.5


class SyncError(ValueError):
    """Nao foi possivel garantir sincronizacao por deslocamento constante."""


@dataclass(frozen=True)
class SyncResult:
    offset_seconds: float
    confidence: float
    drift_seconds: float
    checked_windows: int

    def to_dict(self) -> dict:
        return asdict(self)


def _envelope(path: str) -> tuple[np.ndarray, float]:
    """Le PCM em blocos; nao carrega horas de audio bruto na memoria."""
    logger.info("Lendo audio para sincronizacao: %s", path)
    partes = []
    with wave.open(path, "rb") as wav:
        rate = wav.getframerate()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or rate % ENVELOPE_HZ:
            raise SyncError("Sincronizacao exige WAV mono PCM16 com taxa multipla de 100 Hz.")
        duration = wav.getnframes() / rate
        bloco = rate // ENVELOPE_HZ
        while raw := wav.readframes(bloco * 6000):
            audio = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
            audio = audio[:len(audio) // bloco * bloco]
            if len(audio):
                partes.append(np.sqrt(np.mean(audio.reshape(-1, bloco) ** 2, axis=1)))
    if not partes:
        raise SyncError("Audio vazio; selecione uma fonte com o audio comum ao PGM.")
    envelope = np.concatenate(partes)
    # Compressao logaritmica reduz a influencia de diferencas de ganho.
    escala = max(float(np.percentile(envelope, 90)), 1e-8)
    return np.log1p(envelope / escala * 10), duration


def _correlacao(search: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Correlacao normalizada em todas as posicoes validas, calculada por FFT."""
    n = len(template)
    if n == 0 or len(search) < n:
        return np.empty(0)
    template = template - template.mean()
    energia = float(np.dot(template, template))
    if energia < 1e-10:
        return np.zeros(len(search) - n + 1)
    fft_size = 1 << (len(search) + n - 2).bit_length()
    produto = np.fft.rfft(search, fft_size) * np.fft.rfft(template[::-1], fft_size)
    numerador = np.fft.irfft(produto, fft_size)[n - 1:len(search)]
    soma = np.concatenate(([0.0], np.cumsum(search, dtype=np.float64)))
    soma2 = np.concatenate(([0.0], np.cumsum(search * search, dtype=np.float64)))
    variancia = soma2[n:] - soma2[:-n] - (soma[n:] - soma[:-n]) ** 2 / n
    denominador = np.sqrt(np.maximum(variancia, 0) * energia)
    return np.clip(
        np.divide(numerador, denominador, out=np.zeros_like(numerador), where=denominador > 1e-10),
        -1, 1,
    )


def _pico(scores: np.ndarray, exclusao: int, minimo: float) -> tuple[int, float] | None:
    if not len(scores):
        return None
    scores = np.abs(scores)
    pos = int(np.argmax(scores))
    melhor = float(scores[pos])
    outros = scores.copy()
    outros[max(0, pos - exclusao):pos + exclusao + 1] = 0
    if melhor < minimo or melhor - float(outros.max()) < 0.08:
        return None
    return pos, melhor


def _ler_trecho(path: str, start: float, duration: float) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as wav:
        rate = wav.getframerate()
        pos = max(0, round(start * rate))
        if pos >= wav.getnframes():
            return np.empty(0), rate
        wav.setpos(pos)
        raw = wav.readframes(round(duration * rate))
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768, rate


def sincronizar_audio(pgm_path: str, fonte_path: str, fps: float) -> SyncResult:
    """Busca global e confirmacao por forma de onda no inicio, meio e fim.

    Exige pelo menos 12 s de sobreposicao. Audio repetitivo, silencioso,
    sem correspondencia ou com drift superior a 1,5 quadro e rejeitado.
    """
    pgm, dur_pgm = _envelope(pgm_path)
    fonte, dur_fonte = _envelope(fonte_path)
    curto, longo = (fonte, pgm) if len(fonte) <= len(pgm) else (pgm, fonte)
    fonte_curta = len(fonte) <= len(pgm)
    janela = min(1200, len(curto) // 3)
    if janela < 400:
        raise SyncError("Audio muito curto: sao necessarios pelo menos 12 segundos em comum.")
    candidatos = []
    for start in np.linspace(0, len(curto) - janela, 7).astype(int):
        pico = _pico(_correlacao(longo, curto[start:start + janela]), 100, 0.65)
        if pico is not None:
            pos, _ = pico
            candidatos.append((pos - start) / ENVELOPE_HZ * (1 if fonte_curta else -1))
    if len(candidatos) < 2:
        raise SyncError("Audio sem correspondencia confiavel ou repetitivo. Alinhe manualmente ou pule esta fonte.")
    # Busca inicial aceita pequeno drift; a verificacao fina abaixo o rejeita.
    grupos = [[x for x in candidatos if abs(x - centro) <= 0.5] for centro in candidatos]
    grupo = max(grupos, key=len)
    if len(grupo) < 2 or len(grupo) <= len(candidatos) / 2:
        raise SyncError("Audio com alinhamentos ambiguos. Alinhe manualmente ou pule esta fonte.")
    offset = float(np.median(grupo))
    inicio = max(0.0, offset)
    fim = min(dur_pgm, offset + dur_fonte)
    if fim - inicio < 12:
        raise SyncError("Menos de 12 segundos de audio comum; sincronizacao automatica nao confiavel.")

    # Janelas separadas permitem identificar gravadores com relogios divergentes.
    resultados = []
    for indice, pgm_start in enumerate(np.linspace(inicio + 0.6, fim - 4.6, 5)):
        fonte_start = pgm_start - offset
        # Primeiro relocaliza o envelope numa vizinhanca de 0.5s.
        p = round(pgm_start * ENVELOPE_HZ)
        s = max(0, round((fonte_start - 0.5) * ENVELOPE_HZ))
        template = pgm[p:p + 400]
        local = _pico(_correlacao(fonte[s:s + 500], template), 10, 0.65)
        if local is None:
            continue
        fonte_start = (s + local[0]) / ENVELOPE_HZ
        margem = 0.08
        busca_start = max(0.0, fonte_start - margem)
        ref, rate = _ler_trecho(pgm_path, float(pgm_start), 4)
        busca, rate_fonte = _ler_trecho(fonte_path, busca_start, 4 + 2 * margem)
        if rate != rate_fonte:
            raise SyncError("Taxas de amostragem diferentes na comparacao de audio.")
        pico = _pico(_correlacao(busca, ref), round(rate * 0.005), 0.35)
        if pico is None:
            continue
        pos, score = pico
        deslocamento = round(pgm_start * rate) / rate - (round(busca_start * rate) + pos) / rate
        resultados.append((indice, deslocamento, score))
    offsets = [item[1] for item in resultados]
    drift = max(offsets) - min(offsets) if offsets else 0.0
    limite_drift = MAXIMO_DESVIO_SINCRONIZACAO_QUADROS / fps
    if drift > limite_drift:
        raise SyncError(
            f"Desvio de sincronizacao de {drift:.3f}s ao longo da gravacao "
            f"(mais de {MAXIMO_DESVIO_SINCRONIZACAO_QUADROS:g} quadro(s)). "
            "Corrija a sincronizacao manualmente antes de processar."
        )
    if drift > 1 / fps:
        logger.warning(
            "Sincronizacao aceita com desvio leve de %.3fs (%.2f quadros).",
            drift, drift * fps,
        )
    indices = {item[0] for item in resultados}
    if len(resultados) < 2:
        raise SyncError(
            "Nao foi possivel confirmar o audio em pelo menos dois trechos da gravacao. "
            "Alinhe manualmente ou pule esta fonte."
        )
    if 2 not in indices or resultados[0][0] > 1 or resultados[-1][0] < 3:
        logger.warning(
            "Sincronizacao aceita com %d confirmações, mas sem cobertura completa "
            "de inicio/meio/fim. Revise esta fonte no Premiere.",
            len(resultados),
        )
    result = SyncResult(float(np.median(offsets)), min(x[2] for x in resultados), drift, len(resultados))
    logger.info("Sincronizacao confirmada: %s", result)
    return result
