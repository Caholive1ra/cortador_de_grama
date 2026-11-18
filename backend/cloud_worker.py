"""Servico cloud FastAPI e worker para Supabase.

Execute em processo separado: ``uvicorn cloud_worker:app`` e
``python cloud_worker.py --worker``. A chave service-role fica apenas aqui.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from learning_agent import ask_nemotron, build_prompt, promotion_allowed, validate_proposal

logger = logging.getLogger(__name__)
app = FastAPI(title="Cortador de Grama - aprendizado cloud", version="1.0")


class FeedbackEvent(BaseModel):
    event_id: str = Field(min_length=10, max_length=64)
    idempotency_key: str = Field(min_length=32, max_length=128)
    profile_id: str = Field(min_length=3, max_length=100)
    project_type: str = Field(min_length=3, max_length=40)
    categories: list[str] = Field(default_factory=list, max_length=10)
    metrics: dict[str, float | None] = Field(default_factory=dict)
    examples_count: int = Field(ge=0, le=100000)
    schema_version: int = 1
    content_shared: bool = False


class EvaluationReport(BaseModel):
    """Resultado de uma reexecucao independente, nunca simples diff de XML."""
    reexecution_complete: bool = False
    benchmark_samples: int = Field(ge=0, le=10000)
    useful_removed_delta: float = 0
    unwanted_kept_delta: float = 0
    agreement_delta: float = 0
    metrics: dict[str, Any] = Field(default_factory=dict)


def _token_valid(token: str | None) -> bool:
    expected = os.getenv("CLOUD_LEARNING_INSTALLATION_TOKEN", "")
    return bool(expected and token and hashlib.sha256(token.encode()).digest() == hashlib.sha256(expected.encode()).digest())


def _headers() -> dict[str, str]:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    return {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}


def _supabase(method: str, table: str, *, payload: Any = None, params: dict[str, str] | None = None) -> Any:
    url = os.getenv("SUPABASE_URL", "").rstrip("/") + "/rest/v1/" + table
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        raise RuntimeError("Supabase cloud nao configurado")
    response = httpx.request(method, url, headers=_headers(), json=payload, params=params, timeout=10)
    response.raise_for_status()
    return response.json() if response.content else None


@app.post("/v1/feedback", status_code=202)
def receive_feedback(event: FeedbackEvent, x_installation_token: str | None = Header(default=None)) -> dict:
    if not _token_valid(x_installation_token):
        raise HTTPException(401, "Instalacao nao autorizada")
    if event.content_shared:
        raise HTTPException(422, "Compartilhamento de conteudo nao suportado por este endpoint")
    try:
        _supabase("POST", "editor_feedback", payload={
            "idempotency_key": event.idempotency_key, "profile_id": event.profile_id,
            "project_type": event.project_type, "categories": event.categories,
            "metrics": event.metrics, "examples_count": event.examples_count,
            "content_shared": False,
        }, params={"on_conflict": "idempotency_key"})
        _supabase("POST", "learning_jobs", payload={"profile_id": event.profile_id, "type": "aggregate_feedback", "status": "queued"})
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, "Banco de aprendizado indisponivel") from exc
    return {"accepted": True, "event_id": event.event_id}


@app.get("/v1/configurations/{profile_id}")
def configuration(profile_id: str, x_installation_token: str | None = Header(default=None)) -> dict:
    if not _token_valid(x_installation_token):
        raise HTTPException(401, "Instalacao nao autorizada")
    rows = _supabase("GET", "deployment_versions", params={"profile_id": "eq." + profile_id, "status": "eq.approved", "order": "created_at.desc", "limit": "1"})
    if not rows:
        raise HTTPException(404, "Nenhuma configuracao aprovada")
    return rows[0]["configuration"]


@app.post("/v1/prompt-versions/{version_id}/evaluation")
def submit_evaluation(version_id: str, report: EvaluationReport,
                      x_installation_token: str | None = Header(default=None)) -> dict:
    """Registra avaliação e só publica após benchmark reexecutado aprovado."""
    if not _token_valid(x_installation_token):
        raise HTTPException(401, "Instalacao nao autorizada")
    versions = _supabase("GET", "prompt_versions", params={"id": "eq." + version_id, "limit": "1"})
    if not versions:
        raise HTTPException(404, "Versao de prompt inexistente")
    candidate = versions[0]
    evaluation = report.model_dump()
    approved = promotion_allowed(evaluation)
    _supabase("POST", "evaluation_results", payload={"prompt_version_id": version_id,
              "reexecution_complete": report.reexecution_complete, "benchmark_samples": report.benchmark_samples,
              "metrics": evaluation, "status": "passed" if approved else ("incomplete" if not report.reexecution_complete else "failed")})
    _supabase("PATCH", "prompt_versions", payload={"status": "approved" if approved else ("evaluation_pending" if not report.reexecution_complete else "rejected")}, params={"id": "eq." + version_id})
    if approved:
        configuration = dict(candidate["configuration"])
        configuration["status"] = "approved"
        _supabase("POST", "deployment_versions", payload={"profile_id": candidate["profile_id"],
                  "prompt_version_id": version_id, "configuration": configuration, "status": "approved"})
    return {"approved": approved, "status": "approved" if approved else ("incomplete" if not report.reexecution_complete else "rejected")}


def run_once() -> dict[str, int]:
    """Processa um job; o agendador cloud chama este metodo periodicamente."""
    jobs = _supabase("GET", "learning_jobs", params={"status": "eq.queued", "order": "created_at", "limit": "1"})
    if not jobs:
        return {"processed": 0}
    job = jobs[0]
    _supabase("PATCH", "learning_jobs", payload={"status": "running"}, params={"id": "eq." + job["id"]})
    try:
        feedback = _supabase("GET", "editor_feedback", params={"profile_id": "eq." + job["profile_id"], "select": "categories"})
        counts: dict[str, int] = {}
        for item in feedback:
            for category in item.get("categories", []): counts[category] = counts.get(category, 0) + 1
        current_rows = _supabase("GET", "deployment_versions", params={"profile_id": "eq." + job["profile_id"], "status": "eq.approved", "limit": "1"})
        current = current_rows[0]["configuration"] if current_rows else {"rules": [], "parameters": {}}
        prompt = build_prompt(job["profile_id"], counts, current)
        payload, telemetry = ask_nemotron(prompt)
        try:
            proposal = validate_proposal(payload, job["profile_id"])
        except ValueError as invalid_proposal:
            # Uma correcao limitada melhora a aderencia ao schema sem aceitar
            # valores fora da lista permitida nem executar codigo da resposta.
            repair_prompt = prompt + "\nA resposta anterior foi rejeitada por schema invalido (" + str(invalid_proposal) + "). " \
                "Responda novamente, somente com o JSON valido solicitado."
            payload, retry_telemetry = ask_nemotron(repair_prompt)
            proposal = validate_proposal(payload, job["profile_id"])
            telemetry["schema_repair"] = retry_telemetry
        candidate = {"profile_id": proposal.profile_id, "rules": proposal.rules, "parameters": proposal.parameters,
                     "status": "evaluation_pending", "previous_version_id": current_rows[0]["id"] if current_rows else None}
        _supabase("POST", "prompt_versions", payload={"profile_id": proposal.profile_id, "configuration": candidate,
                  "reason": proposal.reason, "evidence": proposal.evidence, "model": telemetry["model"], "status": "evaluation_pending", "telemetry": telemetry})
        _supabase("PATCH", "learning_jobs", payload={"status": "completed", "result": telemetry}, params={"id": "eq." + job["id"]})
        return {"processed": 1}
    except Exception as exc:
        _supabase("PATCH", "learning_jobs", payload={"status": "failed", "error": type(exc).__name__}, params={"id": "eq." + job["id"]})
        raise


if __name__ == "__main__" and "--worker" in sys.argv:
    while True:
        try: run_once()
        except Exception: logger.exception("Worker cloud falhou")
        time.sleep(float(os.getenv("CLOUD_WORKER_POLL_SECONDS", "30")))
