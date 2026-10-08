"""Falhas temporarias podem ser repetidas; autenticacao invalida nao."""

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

import editorial_ai as ai


def http_error(code, body="", headers=None):
    return HTTPError("https://example.test", code, "test", headers or {}, io.BytesIO(body.encode()))


def test_retry_recupera_503_com_espera_exponencial(monkeypatch):
    esperas = []
    respostas = iter([http_error(503), http_error(503), "ok"])
    monkeypatch.setattr(ai.time, "sleep", esperas.append)

    def operacao():
        resposta = next(respostas)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    assert ai._com_retry_remoto(operacao, "teste") == "ok"
    assert esperas == [2, 4]


def test_gemini_rest_le_todas_as_partes_sem_pensamento():
    resposta = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"thought": True, "text": "Resumo interno"},
        {"text": '{"ok":'}, {"text": 'true}'},
    ]}}]}
    assert json.loads(ai._extrair_json_gemini_rest(resposta)) == {"ok": True}


@pytest.mark.parametrize("motivo", ["MAX_TOKENS", "SAFETY"])
def test_gemini_rest_rejeita_resposta_incompleta(motivo):
    resposta = {"candidates": [{"finishReason": motivo, "content": {
        "parts": [{"text": '{"discard":[]}'}],
    }}]}
    with pytest.raises(ValueError, match=motivo):
        ai._extrair_json_gemini_rest(resposta)


def test_retry_limita_tentativas_e_respeita_retry_after(monkeypatch):
    esperas = []
    chamadas = []
    monkeypatch.setattr(ai.time, "sleep", esperas.append)

    def operacao():
        chamadas.append(1)
        raise http_error(429, headers={"Retry-After": "10"})

    with pytest.raises(HTTPError):
        ai._com_retry_remoto(operacao, "teste")
    assert len(chamadas) == 3
    assert esperas == [10, 10]


def test_retry_nao_repete_quando_gemini_informa_cota_diaria(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ai.time, "sleep", lambda _: pytest.fail("Nao deve repetir cota diaria"))
    erro = http_error(429, "Quota exceeded for metric: generate_content_free_tier_requests. Please retry in 5h.")

    def operacao():
        chamadas.append(1)
        raise erro

    with pytest.raises(HTTPError):
        ai._com_retry_remoto(operacao, "gemini-2.5-flash")
    assert len(chamadas) == 1


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_nao_repete_erros_permanentes(monkeypatch, code):
    monkeypatch.setattr(ai.time, "sleep", lambda _: pytest.fail("Nao deve repetir"))
    erro = http_error(code, "Incorrect API key provided.")
    with pytest.raises(HTTPError):
        ai._com_retry_remoto(lambda: (_ for _ in ()).throw(erro), "teste")
    assert "Incorrect API key" in ai._detalhe_erro_remoto(erro)


def test_nvidia_chave_invalida_tem_acao_clara_sem_segredo(monkeypatch):
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", "segredo-de-teste")
    erro = http_error(400, "Incorrect API key provided. segredo-de-teste")
    monkeypatch.setattr(ai, "_request_url", lambda *a, **kw: (_ for _ in ()).throw(erro))
    with pytest.raises(ai.NvidiaReviewError, match="Substitua NVIDIA_API_KEY") as capturado:
        ai._nvidia_request("teste")
    assert "segredo-de-teste" not in str(capturado.value)
    assert "segredo-de-teste" not in ai._detalhe_erro_remoto(erro)


def test_nvidia_erro_de_limite_nao_e_reportado_como_chave_invalida(monkeypatch):
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", "chave-de-teste")
    erro = http_error(429, "Rate limit exceeded.")
    monkeypatch.setattr(ai, "_request_url", lambda *a, **kw: (_ for _ in ()).throw(erro))
    with pytest.raises(ai.NvidiaReviewError, match="HTTPError") as capturado:
        ai._nvidia_request("teste")
    assert "chave invalida" not in str(capturado.value).lower()
    assert not ai._erro_nvidia_autenticacao(erro)


def test_textual_esgota_retry_antes_de_trocar_modelo(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "teste")
    monkeypatch.setattr(ai, "_modelos_gemini", lambda: ("ocupado", "disponivel"))
    monkeypatch.setattr(ai.time, "sleep", lambda _: None)
    chamadas = []

    def request(method, url, *args, **kwargs):
        chamadas.append(url)
        if "ocupado" in url:
            raise http_error(503)
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}

    monkeypatch.setattr(ai, "_request_url", request)
    assert json.loads(ai._gemini_request("teste")) == {"ok": True}
    assert len(chamadas) == 4
    assert ai.obter_ultimo_modelo_gemini_textual() == "disponivel"


def test_audio_repete_sem_reenviar_arquivo_e_remove_no_final(monkeypatch):
    from google import genai

    uploads, deletes, chamadas = [], [], []
    arquivo = SimpleNamespace(name="files/teste")
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "teste")
    monkeypatch.setattr(ai, "_modelos_gemini", lambda: ("modelo",))
    monkeypatch.setattr(ai.time, "sleep", lambda _: None)

    def generate(**kwargs):
        chamadas.append(kwargs)
        if len(chamadas) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return SimpleNamespace(parsed={"discard": [], "review": []})

    client = SimpleNamespace(
        files=SimpleNamespace(
            upload=lambda **kw: uploads.append(kw) or arquivo,
            delete=lambda **kw: deletes.append(kw),
        ),
        models=SimpleNamespace(generate_content=generate),
    )
    monkeypatch.setattr(genai, "Client", lambda **kw: client)
    assert json.loads(ai._gemini_audio_request("teste", "audio.wav"))["discard"] == []
    assert len(uploads) == 1
    assert len(chamadas) == 2
    assert deletes == [{"name": "files/teste"}]
    assert chamadas[0]["config"]["automatic_function_calling"] == {"disable": True}
