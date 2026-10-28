"""Fila local, persistente e com prioridade para os trabalhos pesados."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)
operacoes_logger = logging.getLogger("operacoes")


class JobExecutionError(Exception):
    """Erro conhecido do trabalho, preservando o codigo HTTP para o painel."""

    def __init__(self, status_code: int, detail: object):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class PriorityJobQueue:
    """Executa um unico trabalho por vez, do menor numero de prioridade ao maior."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    result TEXT,
                    error TEXT
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "title" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            if "attempts" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            if "cancel_requested" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
            # Nunca repete silenciosamente um trabalho interrompido pelo desligamento.
            connection.execute(
                "UPDATE jobs SET status = 'interrupted', finished_at = ?, "
                "error = ? WHERE status = 'running'",
                (time.time(), json.dumps({"status_code": 499, "code": "INTERRUPTED",
                                           "detail": "Backend interrompido. Escolha repetir ou cancelar."})),
            )
            if connection.total_changes:
                logger.warning("Fila: %s tarefa(s) em execucao foram marcadas como interrompidas.", connection.total_changes)

    def enqueue(self, task: str, payload: dict, priority: int, title: str = "") -> dict:
        job_id = uuid4().hex
        created_at = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs (id, task, title, payload, priority, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                (job_id, task, title, json.dumps(payload), priority, created_at),
            )
        self._wake_event.set()
        logger.info("Fila: tarefa criada | id=%s | tipo=%s | prioridade=%s | aula=%s", job_id, task, priority, title)
        operacoes_logger.info("FILA | AGUARDANDO | %s | prioridade=%s", title or task, priority)
        return self.get(job_id) or {"id": job_id, "status": "queued"}

    def get(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            job = self._row_to_job(row)
            if job["status"] == "queued":
                position = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = 'queued' "
                    "AND (priority < ? OR (priority = ? AND created_at < ?))",
                    (job["priority"], job["priority"], job["created_at"]),
                ).fetchone()[0]
                job["queue_position"] = position + 1
                running = connection.execute(
                    "SELECT id, task FROM jobs WHERE status = 'running' LIMIT 1"
                ).fetchone()
                job["running_job"] = (
                    {"id": running["id"], "task": running["task"]}
                    if running else None
                )
            else:
                job["queue_position"] = None
                job["running_job"] = None
            return job

    def list(self, active_only: bool = False, limit: int = 50) -> list[dict]:
        where = "WHERE status IN ('queued', 'running', 'interrupted', 'failed')" if active_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT id FROM jobs {where} ORDER BY "
                "CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
                "priority ASC, created_at ASC LIMIT ?", (limit,),
            ).fetchall()
        return [job for row in rows if (job := self.get(row["id"])) is not None]

    def cancel(self, job_id: str) -> dict | None:
        """Cancela imediatamente se aguarda; em execucao, pede parada segura."""
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ?, error = ? "
                "WHERE id = ? AND status = 'queued'",
                (time.time(), json.dumps({"detail": "Cancelada pelo operador."}), job_id),
            ).rowcount
            if not updated:
                updated = connection.execute(
                    "UPDATE jobs SET cancel_requested = 1, error = ? "
                    "WHERE id = ? AND status = 'running'",
                    (json.dumps({"detail": "Cancelamento solicitado; concluindo a etapa atual com seguranca."}), job_id),
                ).rowcount
        if updated:
            logger.info("Fila: cancelamento solicitado | id=%s", job_id)
            operacoes_logger.info("FILA | CANCELAMENTO SOLICITADO | id=%s", job_id)
        return self.get(job_id) if updated else None

    def retry(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE jobs SET status = 'queued', started_at = NULL, finished_at = NULL, "
                "result = NULL, error = NULL, cancel_requested = 0 WHERE id = ? "
                "AND status IN ('failed', 'interrupted', 'cancelled')", (job_id,),
            ).rowcount
        if updated:
            self._wake_event.set()
            logger.info("Fila: tarefa reenfileirada | id=%s", job_id)
        return self.get(job_id) if updated else None

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict:
        job = {
            "id": row["id"], "task": row["task"], "priority": row["priority"],
            "title": row["title"], "attempts": row["attempts"],
            "cancel_requested": bool(row["cancel_requested"]),
            "status": row["status"], "created_at": row["created_at"],
            "started_at": row["started_at"], "finished_at": row["finished_at"],
        }
        if row["result"]:
            job["result"] = json.loads(row["result"])
        if row["error"]:
            job["error"] = json.loads(row["error"])
        return job

    def start(self, executor) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, args=(executor,), name="priority-job-worker", daemon=True,
        )
        self._thread.start()

    def _claim_next(self) -> dict | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = 'queued' "
                "ORDER BY priority ASC, created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1, "
                "cancel_requested = 0 WHERE id = ?",
                (time.time(), row["id"]),
            )
            connection.commit()
            logger.info("Fila: tarefa iniciada | id=%s | tipo=%s", row["id"], row["task"])
            operacoes_logger.info("FILA | EXECUTANDO | tipo=%s | id=%s", row["task"], row["id"])
            return {"id": row["id"], "task": row["task"], "payload": json.loads(row["payload"])}

    def _finish(self, job_id: str, *, result: object = None, error: object = None) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        cancelled = bool(row and row["cancel_requested"])
        status = "cancelled" if cancelled else ("succeeded" if error is None else "failed")
        if cancelled:
            error = {"detail": "Cancelada pelo operador apos concluir a etapa segura atual."}
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, result = ?, error = ? WHERE id = ?",
                (
                    status, time.time(),
                    json.dumps(result, ensure_ascii=False) if error is None else None,
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    job_id,
                ),
            )
        logger.info("Fila: tarefa finalizada | id=%s | status=%s", job_id, status)
        operacoes_logger.info("FILA | %s | id=%s", status.upper(), job_id)

    def _run(self, executor) -> None:
        while not self._stop_event.is_set():
            job = self._claim_next()
            if job is None:
                self._wake_event.wait(timeout=1.0)
                self._wake_event.clear()
                continue
            try:
                if self.get(job["id"])["cancel_requested"]:
                    self._finish(job["id"])
                    continue
                self._finish(job["id"], result=executor(job["task"], job["payload"]))
            except JobExecutionError as exc:
                logger.warning("Fila: tarefa falhou | id=%s | http=%s | detalhe=%s", job["id"], exc.status_code, exc.detail)
                self._finish(job["id"], error={"status_code": exc.status_code, "detail": exc.detail})
            except Exception as exc:  # O worker continua atendendo a fila apos falhas.
                logger.exception("Fila: falha inesperada | id=%s", job["id"])
                self._finish(job["id"], error={"status_code": 500, "detail": str(exc)})
