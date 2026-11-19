"""Registro portatil das DLLs CUDA usadas pelo Faster-Whisper no Windows."""

from pathlib import Path

import transcriber


def test_registra_todos_os_bins_cuda_da_venv_no_windows(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    esperados = [
        site_packages / "nvidia" / "cuda_runtime" / "bin",
        site_packages / "nvidia" / "cublas" / "bin",
        site_packages / "nvidia" / "cudnn" / "bin",
    ]
    for diretorio in esperados:
        diretorio.mkdir(parents=True)

    chamadas = []
    monkeypatch.setattr(transcriber.os, "add_dll_directory", lambda path: chamadas.append(path) or object())
    handles_anteriores = list(transcriber._CUDA_DLL_DIRECTORY_HANDLES)
    transcriber._CUDA_DLL_DIRECTORY_HANDLES.clear()
    try:
        assert transcriber._registrar_dlls_cuda_windows(site_packages, is_windows=True) == esperados
        assert chamadas == [str(diretorio) for diretorio in esperados]
        assert len(transcriber._CUDA_DLL_DIRECTORY_HANDLES) == 3
    finally:
        transcriber._CUDA_DLL_DIRECTORY_HANDLES[:] = handles_anteriores


def test_nao_registra_dlls_fora_do_windows(tmp_path):
    assert transcriber._registrar_dlls_cuda_windows(Path(tmp_path), is_windows=False) == []
