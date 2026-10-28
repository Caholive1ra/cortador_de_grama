from editorial_cache import chave_editorial_remota, ler_resultado, salvar_resultado


def test_cache_editorial_reutiliza_resultado_com_a_mesma_assinatura(tmp_path, monkeypatch):
    import editorial_cache as cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    fonte = tmp_path / "aula.mp4"
    fonte.write_bytes(b"video")
    chave = chave_editorial_remota(
        str(fonte), {"duration": 420, "fps": 30},
        [{"start": 0, "end": 1, "text": "Teste"}], {"model": "teste"},
    )
    segmentos = [{"start": 0, "end": 1, "enabled": True}]
    salvar_resultado(chave, segmentos, {"mode": "gemini_audio", "status": "ok"})
    assert ler_resultado(chave)["segments"] == segmentos
