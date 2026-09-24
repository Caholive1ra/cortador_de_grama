"""Sugestoes de lettering para a etapa posterior a revisao humana."""

import json
import logging

from editorial_ai import GEMINI_API_KEY, GeminiReviewError, _gemini_request

logger = logging.getLogger(__name__)
MAX_SUGESTOES = 24


class LetteringModelError(RuntimeError):
    """A analise de lettering nao pode ser concluida com seguranca."""


def sugerir_letterings(segmentos: list[dict]) -> list[dict]:
    """Retorna sugestoes ancoradas estritamente nos trechos transcritos.

    A IA nao recebe permissao para inventar tempos: cada sugestao aponta para
    um indice da transcricao final, que e convertido localmente em marcador.
    """
    if not GEMINI_API_KEY:
        raise LetteringModelError(
            "GEMINI_API_KEY ausente. Configure a chave para analisar letterings."
        )
    trechos = [
        {"i": indice, "inicio": round(float(item["start"]), 2),
         "fim": round(float(item["end"]), 2), "texto": str(item["text"]).strip()}
        for indice, item in enumerate(segmentos)
        if str(item.get("text", "")).strip()
    ]
    if not trechos:
        return []

    prompt = """Voce e diretor pedagogico e roteirista de motion graphics de uma videoaula em portugues.
O editor humano ja finalizou os cortes. Leia o contexto completo e sugira
POUCOS letterings que transformem a explicacao oral em apoio visual para o
aluno. Priorize, nesta ordem:
1) um termo tecnico que o professor acabou de introduzir;
2) a definicao simples desse termo (ex.: se ele explica o que e um framework,
escreva "Framework = estrutura reutilizavel para construir software");
3) uma relacao de causa/efeito, regra, passo pratico ou alerta importante.

O texto do lettering deve ser uma sintese didatica escrita por voce, nao uma
frase aleatoria da transcricao. Pode combinar informacoes de ate tres trechos
consecutivos quando isso produzir uma definicao mais correta. Use contexto
anterior e posterior para entender a que termo o professor se refere.
Nao sugira lettering para saudacoes, transicoes, frases vagas, exemplos
isolados, perguntas retoricas ou comentarios de organizacao da aula. Nao
repita literalmente uma frase longa do professor. Se nao houver uma ideia
pedagogica clara, nao sugira nada.

Cada sugestao deve usar EXATAMENTE o indice do trecho em que a ideia termina
de ser explicada e ter texto curto, claro e autocontido (de 1 a 12 palavras).
Um lettering de UMA palavra e valido quando for um termo tecnico, conceito,
nome de metodo ou palavra-chave que o professor esteja apresentando; nesses
casos, nao force uma definicao longa. Para definicoes, prefira o formato
"Termo = definicao". Nao
invente indices, tempos, fatos ou exemplos. No maximo 24 sugestoes, com pelo
menos 8 segundos entre sugestoes sempre que possivel, para nao poluir a aula.
Responda APENAS JSON valido:
{"suggestions":[{"i":0,"text":"...","reason":"conceito|definicao|passo|alerta|formula|conclusao"}]}.
Transcricao final:\n""" + json.dumps(trechos, ensure_ascii=False)
    try:
        resposta = _gemini_request(prompt)
        bruto = _parse_json_resposta(resposta)
    except (GeminiReviewError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Gemini devolveu resposta invalida para lettering: %s", exc)
        return _fallback_letterings(segmentos)

    permitidos = {"conceito", "definicao", "passo", "alerta", "formula", "conclusao"}
    por_indice = {item["i"]: item for item in trechos}
    sugestoes: list[dict] = []
    usados: set[int] = set()
    candidatos = bruto.get("suggestions", bruto.get("letterings", [])) or []
    for item in candidatos:
        try:
            indice = int(item["i"])
            texto = " ".join(str(item["text"]).split()).strip()
            motivo = str(item["reason"])
        except (KeyError, TypeError, ValueError):
            continue
        if indice not in por_indice or indice in usados or motivo not in permitidos:
            continue
        if not texto or len(texto) > 100:
            continue
        trecho = por_indice[indice]
        sugestoes.append({
            "start": trecho["inicio"], "end": trecho["fim"],
            "text": texto, "reason": motivo,
        })
        usados.add(indice)
        if len(sugestoes) == MAX_SUGESTOES:
            break
    logger.info("Analise de lettering gerou %d sugestao(oes).", len(sugestoes))
    return sugestoes or _fallback_letterings(segmentos)


def _parse_json_resposta(resposta: str) -> dict:
    """Aceita JSON puro ou JSON dentro de bloco markdown do provedor."""
    texto = str(resposta or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`").strip()
        if texto.lower().startswith("json"):
            texto = texto[4:].strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        raise ValueError("resposta sem objeto JSON")
    bruto = json.loads(texto[inicio:fim + 1])
    if not isinstance(bruto, dict):
        raise ValueError("resposta JSON nao e um objeto")
    return bruto


def _fallback_letterings(segmentos: list[dict]) -> list[dict]:
    """Fallback conservador: sugere apenas trechos longos e explicativos."""
    resultado = []
    palavras_chave = ("significa", "define", "consiste", "importante", "passo", "permite", "chamamos")
    for item in segmentos:
        texto = " ".join(str(item.get("text", "")).split())
        if len(texto.split()) < 7:
            continue
        if not any(chave in texto.lower() for chave in palavras_chave):
            continue
        resultado.append({
            "start": round(float(item["start"]), 3),
            "end": round(float(item["end"]), 3),
            "text": texto[:80].rstrip(" .,;:") + ("…" if len(texto) > 80 else ""),
            "reason": "conceito",
        })
        if len(resultado) >= 8:
            break
    return resultado
