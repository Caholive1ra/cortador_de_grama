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

    prompt = """Voce e um editor de motion graphics especializado em videoaulas.
O editor humano ja terminou os cortes. Sua tarefa NAO e resumir a aula: e
encontrar momentos em que um pequeno texto na tela aumenta a compreensao.

Para cada trecho, faca mentalmente duas perguntas:
1. O professor acabou de ensinar uma ideia que o aluno precisa lembrar?
2. Um lettering curto tornaria essa ideia mais clara do que apenas ouvir a fala?
Se a resposta for nao, nao crie sugestao.

Crie sugestoes para:
- termo tecnico novo: \"FRAMEWORK\";
- definicao essencial: \"Framework = estrutura reutilizavel\";
- regra ou relacao: \"BAIXO ACOPLAMENTO = MAIS FLEXIBILIDADE\";
- sequencia de procedimento: \"1. DECLARE A INTERFACE\";
- alerta conceitual: \"NAO CONFUNDA CLASSE COM OBJETO\";
- conclusao de um bloco importante.

NAO crie sugestoes para:
- frases de introducao, saudacao ou transicao;
- qualquer frase longa copiada da fala;
- exemplo casual que nao ensina uma regra;
- uma frase que so descreve o que aparece na tela;
- palavras genericas como \"importante\", \"atencao\" ou \"resumo\" sem conteudo.

O campo lettering e literalmente o texto que o designer vai colocar na tela.
Ele deve ter de 1 a 8 palavras e no maximo 60 caracteres. Pode ser apenas
uma palavra quando for o termo ensinado. Escreva em portugues, com capitalizacao
normal ou caixa alta quando for um titulo. Nunca invente informacao.
Escolha o indice do trecho em que a ideia fica compreensivel; use o contexto
dos trechos vizinhos para entender a ideia, mas nao invente timestamps.
Cubra todos os conceitos relevantes da aula: normalmente 3 a 12 sugestoes,
sem parar no primeiro conceito e sem criar duas sugestoes para a mesma ideia.

Retorne APENAS JSON valido neste formato:
{"suggestions":[{"i":12,"lettering":"FRAMEWORK","reason":"conceito","explanation":"Destaca o termo tecnico que esta sendo introduzido."}]}
reason deve ser um destes valores: conceito, definicao, regra, passo, alerta, conclusao.
explanation deve explicar em uma frase por que o aluno se beneficia desse texto.
Transcricao com indices e timestamps:
""" + json.dumps(trechos, ensure_ascii=False)
    try:
        resposta = _gemini_request(prompt)
        bruto = _parse_json_resposta(resposta)
    except (GeminiReviewError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Gemini devolveu resposta invalida para lettering: %s | resposta=%s",
            exc, str(locals().get("resposta", ""))[:2000],
        )
        return _fallback_letterings(segmentos)

    permitidos = {
        "conceito", "definicao", "regra", "passo", "alerta", "formula", "conclusao",
    }
    por_indice = {item["i"]: item for item in trechos}
    sugestoes: list[dict] = []
    usados: set[int] = set()
    candidatos = bruto.get("suggestions", bruto.get("letterings", [])) or []
    for item in candidatos:
        try:
            indice = int(item["i"])
            texto = " ".join(str(item.get("lettering", item.get("text", ""))).split()).strip()
            motivo = str(item["reason"])
            explicacao = " ".join(str(item.get("explanation", item.get("why", ""))).split()).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if indice not in por_indice or indice in usados or motivo not in permitidos:
            continue
        if not texto or len(texto) > 60 or len(texto.split()) > 8:
            continue
        trecho = por_indice[indice]
        sugestoes.append({
            "start": trecho["inicio"], "end": trecho["fim"],
            "text": texto, "reason": motivo,
            "explanation": explicacao or "Resume visualmente uma ideia importante para o aluno.",
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
        rotulo = " ".join(texto.split()[:8]).rstrip(" .,;:")
        resultado.append({
            "start": round(float(item["start"]), 3),
            "end": round(float(item["end"]), 3),
            "text": rotulo[:60],
            "reason": "conceito",
        })
        if len(resultado) >= 8:
            break
    return resultado
