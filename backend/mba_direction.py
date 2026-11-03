"""Sugestões de direção visual para aulas MBA.

Não aplica efeitos: devolve somente recomendações marcadas e explicáveis.
Quando Gemini está disponível, ele recebe poucos frames JPEG selecionados junto
com a transcrição. Sem visão remota, regras locais continuam sugerindo somente
momentos textualmente fortes.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

TIPOS = {"zoom_in", "zoom_out", "hold", "slide_focus", "visual_pause"}
_TERMOS_FORTE = (
    "importante", "atenção", "atencao", "fundamental", "principal", "conceito",
    "essencial", "lembre", "cuidado", "nunca", "sempre", "conclusão", "conclusao",
    "resumo", "ponto-chave", "ponto chave", "por isso",
)


def sugerir_direcao_mba(media_path: str, segmentos: list[dict]) -> dict:
    candidatos = _candidatos(segmentos)
    if not candidatos:
        return {"status": "not_needed", "findings": []}
    visuais = [_frame_jpeg(media_path, item["start"] + min(.6, (item["end"] - item["start"]) / 2))
               for item in candidatos]
    try:
        resposta = _solicitar_visao_gemini(candidatos, visuais)
        sugestoes = _validar_resposta(resposta, candidatos)
        origem = "gemini_vision"
    except Exception as exc:
        logger.info("Direção MBA: visão remota indisponível; usando sugestões locais | %s", exc)
        sugestoes = _sugestoes_locais(candidatos)
        origem = "local"
    return {"status": origem, "findings": [_achado(item) for item in sugestoes]}


def _candidatos(segmentos: list[dict]) -> list[dict]:
    escolhidos = []
    ultimo = -99.0
    for segmento in segmentos:
        texto = str(segmento.get("text", "")).strip()
        inicio, fim = float(segmento.get("start", 0)), float(segmento.get("end", 0))
        baixo = texto.lower()
        forte = any(termo in baixo for termo in _TERMOS_FORTE) or bool(__import__("re").search(r"\b\d+(?:[,.]\d+)?%?\b", baixo))
        if forte and fim > inicio and inicio - ultimo >= 18:
            escolhidos.append({"id": len(escolhidos), "start": inicio, "end": fim, "text": texto[:500]})
            ultimo = inicio
        if len(escolhidos) >= 8:
            break
    return escolhidos


def _frame_jpeg(media_path: str, second: float) -> str | None:
    comando = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{second:.3f}", "-i", media_path,
               "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", "-f", "image2", "-"]
    resultado = subprocess.run(comando, capture_output=True, check=False)
    return base64.b64encode(resultado.stdout).decode("ascii") if resultado.stdout else None


def _solicitar_visao_gemini(candidatos: list[dict], frames: list[str | None]) -> dict:
    from editorial_ai import GEMINI_API_KEY, GEMINI_URL, _extrair_json_gemini_rest, _modelos_gemini, _request_url
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini não configurado")
    prompt = (
        "Você é diretor de edição de uma aula MBA. Analise os frames e as falas. "
        "Sugira no máximo 6 intervenções visuais sutis e úteis, nunca automáticas. "
        "zoom_in: 108-115% para conceito/alerta; zoom_out: transição ou respiro; "
        "hold: manter plano sem movimento; slide_focus: cortar para slide quando ele for visível e relevante; "
        "visual_pause: espaço para GC/gráfico. Evite sugerir zoom em todo item e não invente o que não aparece. "
        "Retorne JSON: {\"suggestions\":[{\"id\":0,\"type\":\"zoom_in|zoom_out|hold|slide_focus|visual_pause\",\"strength\":1,\"duration\":2,\"reason\":\"até 18 palavras\"}]}.\n"
        + json.dumps({"candidates": candidatos}, ensure_ascii=False)
    )
    partes = [{"text": prompt}]
    for item, frame in zip(candidatos, frames):
        if frame:
            partes.extend([{"text": f"Frame do candidato {item['id']} em {item['start']:.1f}s:"},
                           {"inlineData": {"mimeType": "image/jpeg", "data": frame}}])
    payload = {"contents": [{"parts": partes}], "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 2048}}
    erros = []
    for modelo in _modelos_gemini():
        try:
            resposta = _request_url("POST", f"{GEMINI_URL}/{modelo}:generateContent", payload,
                                    {"x-goog-api-key": GEMINI_API_KEY}, timeout=120)
            return json.loads(_extrair_json_gemini_rest(resposta))
        except Exception as exc:
            erros.append(str(exc)[:180])
    raise RuntimeError("Gemini Vision indisponível: " + " | ".join(erros))


def _validar_resposta(resposta: dict, candidatos: list[dict]) -> list[dict]:
    por_id = {item["id"]: item for item in candidatos}
    resultado, vistos = [], set()
    for item in resposta.get("suggestions", [])[:6]:
        if not isinstance(item, dict) or item.get("id") not in por_id or item.get("id") in vistos:
            continue
        tipo = item.get("type")
        if tipo not in TIPOS:
            continue
        base = por_id[item["id"]]
        vistos.add(item["id"])
        resultado.append({**base, "type": tipo, "strength": max(1, min(3, int(item.get("strength", 1)))),
                          "duration": max(1.0, min(3.5, float(item.get("duration", 2)))),
                          "reason": str(item.get("reason", "Reforça a compreensão deste ponto.")).strip()[:180]})
    return resultado


def _sugestoes_locais(candidatos: list[dict]) -> list[dict]:
    resultado = []
    for item in candidatos[:5]:
        texto = item["text"].lower()
        tipo = "zoom_in" if any(x in texto for x in ("importante", "atenção", "atencao", "fundamental", "cuidado")) else "hold"
        resultado.append({**item, "type": tipo, "strength": 1, "duration": 2.0,
                          "reason": "Ponto conceitual relevante; mantenha a intervenção discreta."})
    return resultado


def _achado(item: dict) -> dict:
    nomes = {"zoom_in": "ZOOM-IN SUTIL", "zoom_out": "ZOOM-OUT / RESPIRO", "hold": "MANTER PLANO", "slide_focus": "DESTAQUE PARA SLIDE", "visual_pause": "PAUSA VISUAL"}
    instrucao = {"zoom_in": f"Escala sugerida: 100% → {106 + item['strength'] * 3}%, em {item['duration']:.1f}s.",
                 "zoom_out": f"Abra o enquadramento de forma gradual em {item['duration']:.1f}s.",
                 "hold": "Evite movimento de câmera neste trecho para preservar a explicação.",
                 "slide_focus": "Use somente se o slide/tela estiver legível e complementar a fala.",
                 "visual_pause": "Considere GC, gráfico ou breve respiro visual; não cubra informação importante."}[item["type"]]
    return {"category": "direcao", "color": "purple", "start": round(item["start"], 3),
            "end": round(min(item["end"], item["start"] + item["duration"]), 3),
            "title": "MBA: " + nomes[item["type"]],
            "description": item["reason"] + " " + instrucao,
            "evidence": {"transcript": item["text"], "type": item["type"], "strength": item["strength"], "source": "vision_or_local"}}
