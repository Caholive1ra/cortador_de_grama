"""Cache local e conservador para resultados editoriais remotos."""

import hashlib
import json
import os
import time
from pathlib import Path


CACHE_VERSION = 1
CACHE_DIR = Path(__file__).resolve().parent / "ai_cache"
CACHE_TTL_SECONDS = int(os.getenv("EDITORIAL_CACHE_TTL_SECONDS", "2592000"))


def chave_editorial_remota(
    caminho: str, metadados: dict, segmentos: list[dict], configuracao: dict,
) -> str:
    """Assina fonte, transcricao e configuracao sem guardar audio no cache."""
    arquivo = Path(caminho)
    stat = arquivo.stat()
    origem = {
        "path": str(arquivo.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "duration": round(float(metadados["duration"]), 3),
        "fps": round(float(metadados["fps"]), 5),
    }
    transcricao = [
        {
            "start": round(float(item.get("start", 0)), 3),
            "end": round(float(item.get("end", 0)), 3),
            "text": str(item.get("text", "")).strip(),
        }
        for item in segmentos
    ]
    bruto = json.dumps(
        {"version": CACHE_VERSION, "source": origem, "transcript": transcricao, "config": configuracao},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(bruto).hexdigest()


def ler_resultado(chave: str) -> dict | None:
    caminho = CACHE_DIR / f"{chave}.json"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        if dados.get("version") != CACHE_VERSION:
            return None
        if time.time() - float(dados["created_at"]) > CACHE_TTL_SECONDS:
            return None
        if not isinstance(dados.get("segments"), list) or not isinstance(dados.get("diagnostic"), dict):
            return None
        return dados
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def salvar_resultado(chave: str, segmentos: list[dict], diagnostico: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    destino = CACHE_DIR / f"{chave}.json"
    temporario = destino.with_suffix(".tmp")
    dados = {
        "version": CACHE_VERSION,
        "created_at": time.time(),
        "segments": segmentos,
        "diagnostic": diagnostico,
    }
    temporario.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    temporario.replace(destino)
