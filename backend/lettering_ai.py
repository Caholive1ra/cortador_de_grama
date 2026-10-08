"""Sugestoes de lettering para a etapa posterior a revisao humana."""

import json
import logging

from editorial_ai import (
    GEMINI_API_KEY, NVIDIA_API_KEY, GeminiReviewError, NvidiaReviewError,
    _gemini_request, _nvidia_request,
)

logger = logging.getLogger(__name__)
MAX_SUGESTOES = 24
MAX_CANDIDATOS_ECONOMICOS = 48
MAX_HIGHLIGHTS = 8


class LetteringModelError(RuntimeError):
    """A analise de lettering nao pode ser concluida com seguranca."""


def sugerir_letterings(segmentos: list[dict], modo_economico: bool = False) -> list[dict]:
    """Retorna sugestoes ancoradas estritamente nos trechos transcritos.

    A IA nao recebe permissao para inventar tempos: cada sugestao aponta para
    um indice da transcricao final, que e convertido localmente em marcador.
    """
    if not (GEMINI_API_KEY or NVIDIA_API_KEY):
        raise LetteringModelError(
            "GEMINI_API_KEY e NVIDIA_API_KEY ausentes. Configure ao menos uma chave para analisar letterings."
        )
    trechos = [
        {"i": indice, "inicio": round(float(item["start"]), 2),
         "fim": round(float(item["end"]), 2), "texto": str(item["text"]).strip()}
        for indice, item in enumerate(segmentos)
        if str(item.get("text", "")).strip()
    ]
    if not trechos:
        return []

    trechos_para_ia = _selecionar_trechos_economicos(trechos) if modo_economico else trechos
    prompt = """Voce e um editor de motion graphics especializado em videoaulas.
O editor humano ja terminou os cortes. Sua tarefa e encontrar HIGHLIGHTS: frases
curtas, memoraveis e impactantes que merecem aparecer na tela, alem de poucos
textos didaticos que realmente aumentem a compreensao. NAO faca um resumo.

Para cada trecho, faca mentalmente duas perguntas:
1. O professor acabou de ensinar uma ideia que o aluno precisa lembrar?
2. Um lettering curto tornaria essa ideia mais clara do que apenas ouvir a fala?
Se a resposta for nao, nao crie sugestao.

PRIORIDADE 1 — HIGHLIGHTS (normalmente 3 a 8 por aula):
- uma afirmacao forte que sustenta uma ideia central;
- uma conclusao, contraste, alerta ou principio que o aluno deve lembrar;
- uma frase que funciona sozinha fora do contexto da conversa.
O highlight deve ser uma frase dita pelo participante, copiada literalmente ou
encurtada APENAS pela remocao de muletas como "ne", "entao" e "tipo". Nunca
parafraseie, complete ou invente uma frase de efeito. Prefira frases
declarativas; nao use perguntas soltas, saudacoes, nomes de pessoas ou falas
que dependam da frase anterior para fazer sentido.

PRIORIDADE 2 — textos didaticos, somente quando forem mais uteis que um highlight:
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
Ele deve ter de 1 a 12 palavras e no maximo 200 caracteres. Pode ser apenas
uma palavra quando for o termo ensinado. Escreva em portugues, com capitalizacao
normal ou caixa alta quando for um titulo. Nunca invente informacao.
Escolha o indice do trecho em que a ideia fica compreensivel; use o contexto
dos trechos vizinhos para entender a ideia, mas nao invente timestamps.
Selecione qualidade, nao quantidade: normalmente 3 a 12 sugestoes no total.
Nao crie duas sugestoes para a mesma ideia e mantenha no maximo 8 highlights.

Retorne APENAS JSON valido neste formato:
{"suggestions":[{"i":12,"lettering":"Seguranca nao e um produto, e um processo","reason":"highlight","explanation":"Frase central, completa e memoravel dita pelo convidado."}]}
reason deve ser um destes valores: highlight, conceito, definicao, regra, passo, alerta, conclusao.
explanation deve explicar em uma frase por que o aluno se beneficia desse texto.
    Transcricao com indices e timestamps:
    """ + json.dumps(trechos_para_ia, ensure_ascii=False)
    try:
        resposta = _solicitar_json_para_lettering(prompt)
        bruto = _parse_json_resposta(resposta)
    except (GeminiReviewError, NvidiaReviewError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Gemini/NVIDIA devolveu resposta invalida para lettering: %s | resposta=%s",
            exc, str(locals().get("resposta", ""))[:2000],
        )
        return _fallback_letterings(segmentos)

    permitidos = {
        "highlight", "conceito", "definicao", "regra", "passo", "alerta", "formula", "conclusao",
    }
    por_indice = {item["i"]: item for item in trechos}
    sugestoes: list[dict] = []
    usados: set[int] = set()
    highlights = 0
    candidatos = bruto.get("suggestions", bruto.get("letterings", [])) or []
    for item in candidatos:
        try:
            indice = int(item["i"])
            texto = " ".join(str(item.get("lettering", item.get("highlight", item.get("text", "")))).split()).strip()
            motivo = str(item["reason"])
            explicacao = " ".join(str(item.get("explanation", item.get("why", ""))).split()).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if indice not in por_indice or indice in usados or motivo not in permitidos:
            continue
        if not texto or len(texto) > 60 or len(texto.split()) > 12:
            continue
        if motivo == "highlight" and (highlights >= MAX_HIGHLIGHTS or not _highlight_ancorado(texto, por_indice[indice]["texto"])):
            continue
        trecho = por_indice[indice]
        sugestoes.append({
            "start": trecho["inicio"], "end": trecho["fim"],
            "text": texto, "reason": motivo,
            "explanation": explicacao or "Resume visualmente uma ideia importante para o aluno.",
        })
        usados.add(indice)
        highlights += motivo == "highlight"
        if len(sugestoes) == MAX_SUGESTOES:
            break
    logger.info("Analise de lettering gerou %d sugestao(oes).", len(sugestoes))
    return sugestoes or _fallback_letterings(segmentos)


def _solicitar_json_para_lettering(prompt: str) -> str:
    """Mantem Gemini como preferencial e usa NVIDIA se ele falhar."""
    erros: list[str] = []
    if GEMINI_API_KEY:
        try:
            return _gemini_request(prompt)
        except GeminiReviewError as exc:
            erros.append(f"Gemini: {exc}")
    if NVIDIA_API_KEY:
        try:
            return _nvidia_request(prompt)
        except NvidiaReviewError as exc:
            erros.append(f"NVIDIA: {exc}")
    raise LetteringModelError("Nenhum provedor textual respondeu: " + " | ".join(erros))


def _selecionar_trechos_economicos(trechos: list[dict]) -> list[dict]:
    """Mantem candidatos didaticos e pouco contexto para reduzir tokens."""
    pistas = (
        "significa", "defin", "consiste", "chamamos", "importante", "regra",
        "passo", "primeiro", "segundo", "terceiro", "formula", "conclus",
        "aten", "nao confunda", "diferen", "permite", "nao e", "nao basta",
        "o mais", "principal", "fundamental", "muda", "precisa",
    )
    selecionados: set[int] = set()
    for posicao, trecho in enumerate(trechos):
        texto = str(trecho["texto"]).lower()
        if any(pista in texto for pista in pistas):
            selecionados.update(range(max(0, posicao - 1), min(len(trechos), posicao + 2)))
    if not selecionados:
        return trechos[:min(len(trechos), MAX_CANDIDATOS_ECONOMICOS)]
    return [trechos[posicao] for posicao in sorted(selecionados)[:MAX_CANDIDATOS_ECONOMICOS]]


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
        rotulo = " ".join(texto.split()[:12]).rstrip(" .,;:")
        resultado.append({
            "start": round(float(item["start"]), 3),
            "end": round(float(item["end"]), 3),
            "text": rotulo[:60],
            "reason": "conceito",
        })
        if len(resultado) >= 8:
            break
    return resultado


def _highlight_ancorado(highlight: str, trecho: str) -> bool:
    """Highlights sao citas; aceita apenas palavras que vieram do trecho."""
    palavras_highlight = set(_normalizar_palavras(highlight))
    palavras_trecho = set(_normalizar_palavras(trecho))
    # Palavras muito curtas nao comprovam que a frase foi dita.
    relevantes = {palavra for palavra in palavras_highlight if len(palavra) > 2}
    return bool(relevantes) and relevantes.issubset(palavras_trecho)


def _normalizar_palavras(texto: str) -> list[str]:
    import re
    import unicodedata
    normalizado = "".join(
        caractere for caractere in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(caractere) != "Mn"
    )
    return re.findall(r"[a-z0-9]+", normalizado)
