"""Cache local de transcrições para evitar refazer Whisper no mesmo render."""

import hashlib
import json
import os
import time
from pathlib import Path


CACHE_VERSION = 1
CACHE_DIR = Path(__file__).resolve().parent / "transcription_cache"
CACHE_TTL_SECONDS = int(os.getenv("TRANSCRIPTION_CACHE_TTL_SECONDS", "2592000"))


def _key(media_path: str, metadata: dict) -> str:
    source = Path(media_path)
    stat = source.stat()
    payload = {
        "version": CACHE_VERSION,
        "path": str(source.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "duration": round(float(metadata["duration"]), 3),
        "video_start": round(float(metadata.get("video_start", 0)), 3),
        "whisper_model": os.getenv("WHISPER_MODEL", "base"),
        "whisper_device": os.getenv("WHISPER_DEVICE", "auto"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def get_or_transcribe(media_path: str, metadata: dict, audio_path: str, transcribe) -> tuple[list[dict], bool]:
    """Devolve a transcrição válida ou a gera uma vez e a grava atomicamente."""
    try:
        key = _key(media_path, metadata)
    except OSError:
        # Chamadas de teste e integrações podem fornecer uma fonte virtual;
        # nesse caso a transcrição continua funcionando sem cache.
        return transcribe(audio_path), False
    target = CACHE_DIR / f"{key}.json"
    try:
        saved = json.loads(target.read_text(encoding="utf-8"))
        if (saved.get("version") == CACHE_VERSION
                and time.time() - float(saved["created_at"]) <= CACHE_TTL_SECONDS
                and isinstance(saved.get("segments"), list)):
            return saved["segments"], True
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    segments = transcribe(audio_path)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": CACHE_VERSION, "created_at": time.time(),
                                     "segments": segments}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return segments, False
