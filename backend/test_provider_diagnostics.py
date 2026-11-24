import editorial_ai as ai


def test_sonda_nao_executa_por_padrao(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "teste")
    monkeypatch.setattr(ai, "_request_url", lambda *args: (_ for _ in ()).throw(AssertionError("nao chama")))
    resultado = ai.diagnosticar_provedor_editorial("gemini")
    assert resultado["executed"] is False
    assert resultado["model"] == ai.GEMINI_MODEL


def test_endpoint_de_diagnostico_e_opt_in(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(main, "diagnosticar_provedor_editorial", lambda provider, model, execute: {
        "provider": provider, "model": model, "executed": execute,
    })

    resposta = TestClient(main.app).post("/diagnostics/providers/gemini", json={})

    assert resposta.status_code == 200
    assert resposta.json() == {"provider": "gemini", "model": None, "executed": False}


def test_sonda_glm_registra_raciocinio_sem_aceitar_resposta_vazia(monkeypatch):
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", "teste")
    monkeypatch.setattr(ai, "NVIDIA_MODELS", ("z-ai/glm-5.3-flash",))
    chamada = {}

    def responder(_method, _endpoint, payload, _headers, timeout):
        chamada.update(payload=payload, timeout=timeout)
        return {"usage": {"prompt_tokens": 3, "completion_tokens": 9, "total_tokens": 12},
                "choices": [{"finish_reason": "stop", "message": {"content": "", "reasoning_content": "pensamento"}}]}

    monkeypatch.setattr(ai, "_request_url", responder)
    resultado = ai.diagnosticar_provedor_editorial("nvidia", "z-ai/glm-5.3-flash", executar=True)
    assert chamada["payload"]["reasoning_effort"] == "low"
    assert chamada["payload"]["chat_template_kwargs"] == {"clear_thinking": True}
    assert resultado["response_chars"] == 0
    assert resultado["editorial_schema_valid"] is False
    assert resultado["stage"] == "validation"


def test_sonda_gemma_valida_schema_editorial(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "teste")
    monkeypatch.setattr(ai, "GEMMA_MODEL", "gemma-4-26b-a4b-it")
    monkeypatch.setattr(ai, "_request_url", lambda *args, **kwargs: {
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 4, "totalTokenCount": 6},
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{\"discard\":[],\"review\":[]}'}]}}],
    })
    resultado = ai.diagnosticar_provedor_editorial("gemma", executar=True)
    assert resultado["http_status"] == 200
    assert resultado["json_valid"] is True
    assert resultado["editorial_schema_valid"] is True


def test_sonda_rejeita_json_quando_finish_reason_indica_truncamento(monkeypatch):
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", "teste")
    monkeypatch.setattr(ai, "NVIDIA_MODELS", ("mistralai/mistral-large-4-1t-a52b",))
    monkeypatch.setattr(ai, "_request_url", lambda *args, **kwargs: {
        "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
        "choices": [{"finish_reason": "length", "message": {"content": '{\"discard\":[],\"review\":[]}'}}],
    })

    resultado = ai.diagnosticar_provedor_editorial("nvidia", executar=True)

    assert resultado["json_valid"] is True
    assert resultado["editorial_schema_valid"] is False
    assert resultado["stage"] == "truncated"
