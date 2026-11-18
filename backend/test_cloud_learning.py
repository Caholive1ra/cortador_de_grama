import json

import httpx
import pytest

import cloud_learning as cloud
from learning_agent import PROPOSAL_TOOL_NAME, ask_nemotron, promotion_allowed, validate_proposal


@pytest.fixture(autouse=True)
def isolated_cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cloud, "OUTBOX_PATH", tmp_path / "outbox.sqlite")
    monkeypatch.setattr(cloud, "CONFIG_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.delenv("CLOUD_LEARNING_ENABLED", raising=False)
    monkeypatch.delenv("CLOUD_LEARNING_API_URL", raising=False)
    monkeypatch.delenv("CLOUD_LEARNING_INSTALLATION_TOKEN", raising=False)


def record():
    return {"project_type": "videoaula", "learning_profile": {"id": "videoaula_editorial_v1"},
            "source_group": "nao-enviado", "comparison": {"metrics": {"agreement_rate": .8, "useful_removed_seconds": 1, "unwanted_kept_seconds": 2}}, "examples": [{}, {}]}


def test_feedback_sem_categoria_e_idempotente():
    first = cloud.enqueue_feedback(record())
    second = cloud.enqueue_feedback(record())
    assert first["queued"] and not first["duplicate"]
    assert second["duplicate"] and cloud.pending_outbox() == 1


def test_evento_nunca_envia_xml_transcricao_caminho_ou_observacao():
    cloud.enqueue_feedback(record(), ["repeticoes_excessivas_nao_identificadas"], "texto confidencial")
    with cloud._connect() as conn:
        payload = conn.execute("select payload from cloud_outbox").fetchone()[0]
    assert "confidencial" not in payload and "xml" not in payload.lower() and "transcri" not in payload.lower()


def test_indisponibilidade_mantem_outbox(monkeypatch):
    monkeypatch.setenv("CLOUD_LEARNING_ENABLED", "true")
    monkeypatch.setenv("CLOUD_LEARNING_API_URL", "https://cloud.test")
    monkeypatch.setenv("CLOUD_LEARNING_INSTALLATION_TOKEN", "token")
    cloud.enqueue_feedback(record())
    monkeypatch.setattr(cloud.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    assert cloud.flush_outbox()["pending"] == 1


def test_configuracao_aprovada_usa_cache_quando_cloud_falha(monkeypatch):
    config = {"profile_id": "videoaula_editorial_v1", "status": "approved", "rules": ["Preserve exemplos didaticos."], "parameters": {}}
    cloud.CONFIG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cloud.CONFIG_CACHE_PATH.write_text(json.dumps({"videoaula_editorial_v1": config}), encoding="utf-8")
    assert cloud.refresh_configuration("videoaula_editorial_v1")["configuration"] == config


def test_schema_do_agente_e_promocao_exigem_reexecucao():
    valid = {"rules": ["Marque repeticoes acidentais apenas com contexto de retake."], "parameters": {"repetition_similarity": .8}, "reason": "Tendencia agregada", "evidence": {"repeticoes": 8}}
    assert validate_proposal(valid, "videoaula_editorial_v1").rules
    with pytest.raises(ValueError):
        validate_proposal({"rules": ["x"], "parameters": {}, "reason": "x", "evidence": {}}, "p")
    assert not promotion_allowed({"reexecution_complete": False, "benchmark_samples": 99})
    assert promotion_allowed({"reexecution_complete": True, "benchmark_samples": 5, "useful_removed_delta": 0, "unwanted_kept_delta": -1, "agreement_delta": .01})


def test_agente_usa_function_calling_para_schema(monkeypatch):
    monkeypatch.setenv("NVIDIA_LEARNING_API_KEY", "chave-de-teste")
    captured = {}

    class Response:
        is_error = False
        def raise_for_status(self): pass
        def json(self):
            return {"model": "nvidia/nemotron-3-ultra-550b-a55b", "choices": [{"message": {"content": "{}"}}], "usage": {}}

    def request(*args, **kwargs):
        captured.update(kwargs)
        return Response()

    ask_nemotron("teste", request=request)
    assert captured["json"]["tool_choice"]["function"]["name"] == PROPOSAL_TOOL_NAME
    assert captured["json"]["tools"][0]["function"]["name"] == PROPOSAL_TOOL_NAME


def test_agente_le_argumentos_da_function_call(monkeypatch):
    monkeypatch.setenv("NVIDIA_LEARNING_API_KEY", "chave-de-teste")
    proposal = {"rules": ["Preserve exemplos didaticos em explicacoes importantes."], "parameters": {}, "reason": "Teste", "evidence": {"x": 1}}

    class Response:
        is_error = False
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"tool_calls": [{"function": {"name": PROPOSAL_TOOL_NAME, "arguments": json.dumps(proposal)}}]}}], "usage": {}}

    payload, _ = ask_nemotron("teste", request=lambda *args, **kwargs: Response())
    assert payload == proposal


def test_agente_recusa_similarity_fora_da_faixa():
    invalid = {"rules": ["Preserve exemplos didaticos em explicacoes importantes."],
               "parameters": {"repetition_similarity": 2}, "reason": "Teste", "evidence": {}}
    with pytest.raises(ValueError, match="Parametros invalidos"):
        validate_proposal(invalid, "videoaula_editorial_v1")
