from transcription_cache import get_or_transcribe


def test_reutiliza_transcricao_da_mesma_midia(tmp_path, monkeypatch) -> None:
    media = tmp_path / "aula.mp4"
    media.write_bytes(b"video")
    monkeypatch.setattr("transcription_cache.CACHE_DIR", tmp_path / "cache")
    calls = []

    def transcribe(audio_path):
        calls.append(audio_path)
        return [{"start": 0, "end": 1, "text": "Olá"}]

    metadata = {"duration": 1, "video_start": 0}
    first, hit_first = get_or_transcribe(str(media), metadata, "audio.wav", transcribe)
    second, hit_second = get_or_transcribe(str(media), metadata, "audio.wav", transcribe)

    assert first == second
    assert hit_first is False
    assert hit_second is True
    assert calls == ["audio.wav"]
