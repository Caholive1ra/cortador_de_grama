"""Confere uma chamada curta ao Kimi sem imprimir credenciais."""

import json

import editorial_ai as ai


def main() -> None:
    try:
        payload = {
            "model": ai.NVIDIA_MODEL,
            "messages": [{"role": "user", "content": "Return only JSON: {\"ok\": true}"}],
            "max_tokens": 16384,
            "seed": 0,
            "stream": False,
            "temperature": 1,
            "reasoning_effort": ai.NVIDIA_REASONING_EFFORT,
        }
        resposta = ai._request_url(
            "POST", f"{ai.NVIDIA_BASE_URL}/chat/completions", payload,
            {"Authorization": f"Bearer {ai.NVIDIA_API_KEY}", "Accept": "application/json"}, timeout=180,
        )
        escolha = resposta.get("choices", [{}])[0]
        mensagem = escolha.get("message", {})
        print(json.dumps({
            "authenticated": True, "model": resposta.get("model"),
            "finish_reason": escolha.get("finish_reason"),
            "content_length": len(str(mensagem.get("content") or "")),
            "reasoning_length": len(str(mensagem.get("reasoning_content") or "")),
            "usage": resposta.get("usage"),
        }))
    except Exception as exc:
        causa = exc.__cause__ or exc
        print(json.dumps({"error": ai._detalhe_erro_remoto(causa)}, ensure_ascii=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
