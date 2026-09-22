"""Sinais controlados com deslocamentos, lacunas, ruido e drift conhecidos."""

import wave

import numpy as np
import pytest

from audio_sync import SyncError, _correlacao, sincronizar_audio

RATE = 16000


def _sinal(seconds: int = 60, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = seconds * RATE
    # Ruido modulado simula a variacao de energia de uma fala nao repetitiva.
    envelope = np.interp(np.arange(n), np.arange(0, n, 800), rng.uniform(0.01, 0.35, (n + 799) // 800))
    return rng.normal(size=n) * envelope


def _wav(path, signal: np.ndarray) -> str:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes((np.clip(signal, -1, 1) * 30000).astype("<i2").tobytes())
    return str(path)


def test_correlacao_fft_equivale_a_normalizada() -> None:
    rng = np.random.default_rng(0)
    search = rng.normal(size=100)
    template = search[33:43]
    expected = [np.corrcoef(search[i:i + 10], template)[0, 1] for i in range(91)]
    np.testing.assert_allclose(_correlacao(search, template), expected, atol=1e-12)


@pytest.mark.parametrize("offset", [0.0, 5.123, -7.217])
def test_alinha_fontes_mais_curtas_longas_e_polaridade_invertida(tmp_path, offset: float) -> None:
    signal = _sinal(80)
    pgm = signal[10 * RATE:70 * RATE]
    inicio = round((10 + offset) * RATE)
    fonte = signal[inicio:75 * RATE] * -0.6
    result = sincronizar_audio(_wav(tmp_path / "pgm.wav", pgm), _wav(tmp_path / "fonte.wav", fonte), 29.97)
    assert result.offset_seconds == pytest.approx(offset, abs=0.001)
    assert result.checked_windows >= 3
    assert result.drift_seconds < 0.001


@pytest.mark.parametrize("tipo", ["silencio", "diferente", "repetido", "curto", "drift"])
def test_rejeita_sincronizacao_insegura(tmp_path, tipo: str) -> None:
    pgm = _sinal()
    fonte = pgm.copy()
    if tipo == "silencio":
        fonte[:] = 0
    elif tipo == "diferente":
        fonte = _sinal(seed=123)
    elif tipo == "repetido":
        pgm = fonte = np.tile(_sinal(4), 15)
    elif tipo == "curto":
        fonte = fonte[:8 * RATE]
    else:
        # 0.06s de deslocamento acumulado em 60s, superior a um quadro.
        fonte = np.interp(np.arange(len(pgm)) * 1.001, np.arange(len(pgm)), pgm)
    with pytest.raises(SyncError):
        sincronizar_audio(_wav(tmp_path / "pgm.wav", pgm), _wav(tmp_path / "fonte.wav", fonte), 30)


def test_detecta_mudanca_de_offset_entre_inicio_e_fim(tmp_path) -> None:
    pgm = _sinal()
    fonte = pgm.copy()
    corte = 30 * RATE
    atraso = round(0.08 * RATE)
    fonte[corte + atraso:] = pgm[corte:-atraso]
    with pytest.raises(SyncError, match="Desvio de sincronizacao"):
        sincronizar_audio(_wav(tmp_path / "pgm.wav", pgm), _wav(tmp_path / "fonte.wav", fonte), 30)
