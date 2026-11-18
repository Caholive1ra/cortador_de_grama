"""Sincronizacao segura e nao bloqueante do aprendizado editorial compartilhado.

O backend local nunca envia video, audio, XML, transcricao ou caminhos locais.
Ele grava um evento minimo em uma outbox SQLite e tenta entrega-lo ao servico
cloud configurado. Falhas de rede preservam a outbox para a proxima execucao.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent / "learning_data"
OUTBOX_PATH = DATA_DIR / "cloud_outbox.sqlite3"
CONFIG_CACHE_PATH = DATA_DIR / "editorial_config_cache.json"


def _enabled() -> bool:
    return os.getenv("CLOUD_LEARNING_ENABLED", "false").lower() == "true"


def _base_url() -> str:
    return os.getenv("CLOUD_LEARNING_API_URL", "").rstrip("/")


def _token() -> str:
    return os.getenv("CLOUD_LEARNING_INSTALLATION_TOKEN", "")


def cloud_status() -> dict[str, Any]:
    return {"enabled": _enabled(), "configured": bool(_base_url() and _token()),
            "content_sharing": False}


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(OUTBOX_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_outbox (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL, created_at REAL NOT NULL, sent_at REAL, last_error TEXT
    )""")
    return connection


def _safe_event(record: dict[str, Any], categories: list[str], note: str) -> dict[str, Any]:
    """Transforma o registro local em dados agregados, sem texto editorial."""
    comparison = record.get("comparison", {})
    metrics = comparison.get("metrics", {})
    allowed = {
        "palavras_repetidas_nao_removidas", "silencios_mantidos_indevidamente",
        "repeticoes_excessivas_nao_identificadas", "cortes_excessivamente_pequenos",
        "erros_de_fala_mantidos", "conteudo_importante_removido",
        "pontos_de_corte_imprecisos", "cortes_desnecessarios",
        "pausas_naturais_removidas", "outros",
    }
    selected = sorted({str(value) for value in categories if str(value) in allowed})
    # Observacao e intencionalmente opt-in, limitada e enviada apenas se a
    # instalacao habilitar compartilhamento textual no futuro. Hoje nao sai.
    del note
    fingerprint = hashlib.sha256(json.dumps({
        "record_id": record.get("id"),
        "source_group": record.get("source_group"), "profile": record.get("learning_profile", {}).get("id"),
        "metrics": metrics, "categories": selected,
    }, sort_keys=True).encode()).hexdigest()
    return {
        "event_id": str(uuid4()), "idempotency_key": fingerprint,
        "profile_id": record.get("learning_profile", {}).get("id"),
        "project_type": record.get("project_type"), "categories": selected,
        "metrics": {key: metrics.get(key) for key in (
            "agreement_rate", "useful_removed_seconds", "unwanted_kept_seconds",
        )},
        "examples_count": len(record.get("examples", [])),
        "schema_version": 1, "content_shared": False,
    }


def enqueue_feedback(record: dict[str, Any], categories: list[str] | None = None, note: str = "") -> dict[str, Any]:
    event = _safe_event(record, categories or [], note)
    with _connect() as connection:
        exists = connection.execute("SELECT id FROM cloud_outbox WHERE json_extract(payload, '$.idempotency_key') = ?", (event["idempotency_key"],)).fetchone()
        if exists:
            return {"queued": True, "duplicate": True, "event_id": exists["id"]}
        connection.execute("INSERT INTO cloud_outbox (id, payload, next_attempt, created_at) VALUES (?, ?, ?, ?)",
                           (event["event_id"], json.dumps(event), time.time(), time.time()))
    return {"queued": True, "duplicate": False, "event_id": event["event_id"]}


def flush_outbox(limit: int = 10) -> dict[str, int]:
    """Entrega eventos vencidos; nunca levanta erro para o fluxo do editor."""
    if not (_enabled() and _base_url() and _token()):
        return {"sent": 0, "pending": pending_outbox(), "skipped": 1}
    sent = 0
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM cloud_outbox WHERE sent_at IS NULL AND next_attempt <= ? ORDER BY created_at LIMIT ?", (time.time(), limit)).fetchall()
        for row in rows:
            try:
                response = httpx.post(_base_url() + "/v1/feedback", content=row["payload"], headers={
                    "Content-Type": "application/json", "X-Installation-Token": _token(),
                    "Idempotency-Key": json.loads(row["payload"])["idempotency_key"],
                }, timeout=3.0)
                response.raise_for_status()
                connection.execute("UPDATE cloud_outbox SET sent_at = ? WHERE id = ?", (time.time(), row["id"]))
                sent += 1
            except Exception as exc:  # rede e servico sao opcionais
                attempts = row["attempts"] + 1
                delay = min(3600, 2 ** min(attempts, 10))
                connection.execute("UPDATE cloud_outbox SET attempts=?, next_attempt=?, last_error=? WHERE id=?",
                                   (attempts, time.time() + delay, type(exc).__name__, row["id"]))
                logger.info("Aprendizado cloud indisponivel; evento preservado na outbox: %s", type(exc).__name__)
    return {"sent": sent, "pending": pending_outbox(), "skipped": 0}


def pending_outbox() -> int:
    with _connect() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM cloud_outbox WHERE sent_at IS NULL").fetchone()[0])


def get_cached_configuration(profile_id: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(CONFIG_CACHE_PATH.read_text(encoding="utf-8"))
        config = payload.get(profile_id)
        return config if isinstance(config, dict) and config.get("status") == "approved" else None
    except (OSError, ValueError, TypeError):
        return None


def refresh_configuration(profile_id: str) -> dict[str, Any]:
    """Atualiza o cache somente entre jobs; configuracao anterior sobrevive a falhas."""
    cached = get_cached_configuration(profile_id)
    if not (_enabled() and _base_url() and _token()):
        return {"configuration": cached, "source": "cache", "updated": False}
    try:
        response = httpx.get(_base_url() + "/v1/configurations/" + profile_id,
                             headers={"X-Installation-Token": _token()}, timeout=2.0)
        if response.status_code == 404:
            return {"configuration": cached, "source": "cache", "updated": False}
        response.raise_for_status()
        config = response.json()
        _validate_configuration(config, profile_id)
        CONFIG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        all_configs = {}
        try:
            all_configs = json.loads(CONFIG_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        all_configs[profile_id] = config
        CONFIG_CACHE_PATH.write_text(json.dumps(all_configs, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"configuration": config, "source": "cloud", "updated": True}
    except Exception as exc:
        logger.info("Configuracao cloud indisponivel; usando cache: %s", type(exc).__name__)
        return {"configuration": cached, "source": "cache", "updated": False}


def _validate_configuration(config: dict[str, Any], profile_id: str) -> None:
    if not isinstance(config, dict) or config.get("profile_id") != profile_id or config.get("status") != "approved":
        raise ValueError("Configuracao editorial invalida")
    rules = config.get("rules", [])
    if not isinstance(rules, list) or len(rules) > 30 or any(not isinstance(rule, str) or len(rule) > 500 for rule in rules):
        raise ValueError("Regras editoriais invalidas")
