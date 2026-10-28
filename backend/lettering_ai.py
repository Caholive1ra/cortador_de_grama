"""Revisão contextual de lettering via provedores configurados."""

import logging

from editorial_ai import (
    GEMINI_API_KEY, NVIDIA_API_KEY, GeminiReviewError, GemmaReviewError,
    NvidiaReviewError, _gemini_request, _gemma_request, _nvidia_request,
)

logger = logging.getLogger(__name__)


class LetteringModelError(RuntimeError):
    """A revisão não pôde ser concluída com segurança."""


def sugerir_letterings(segmentos: list[dict], modo_economico: bool = False, contexto_curso: str = "") -> list[dict]:
    from lettering_context import selecionar, siglas_obrigatorias
    if not (GEMINI_API_KEY or NVIDIA_API_KEY):
        raise LetteringModelError("Configure GEMINI_API_KEY ou NVIDIA_API_KEY.")
    try:
        logger.info("Lettering IA: inicio | segmentos=%s | economico=%s | contexto=%s caracteres",
                    len(segmentos), modo_economico, len(contexto_curso or ""))
        sugestoes = selecionar(segmentos, _solicitar_json_para_lettering, contexto_curso, modo_economico)
        logger.info("Lettering IA: selecao contextual concluida | sugestoes=%s", len(sugestoes))
        return sugestoes
    except (LetteringModelError, ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "Revisao contextual nao concluiu (%s: %s); entregando somente siglas detectadas localmente.",
            type(exc).__name__, exc,
        )
        return siglas_obrigatorias(segmentos)



def _solicitar_json_para_lettering(prompt: str) -> str:
    """Usa Gemini, GPT-OSS e Gemma nesta ordem para revisar letterings."""
    erros: list[str] = []
    if GEMINI_API_KEY:
        try:
            logger.info("Lettering IA: consultando Gemini")
            return _gemini_request(prompt)
        except GeminiReviewError as exc:
            logger.warning("Lettering IA: Gemini falhou | %s", exc)
            erros.append(f"Gemini: {exc}")
    if NVIDIA_API_KEY:
        try:
            logger.info("Lettering IA: consultando GPT-OSS (NVIDIA)")
            return _nvidia_request(prompt)
        except NvidiaReviewError as exc:
            logger.warning("Lettering IA: GPT-OSS falhou | %s", exc)
            erros.append(f"GPT-OSS: {exc}")
    if GEMINI_API_KEY:
        try:
            logger.info("Lettering IA: consultando Gemma")
            return _gemma_request(prompt)
        except GemmaReviewError as exc:
            logger.warning("Lettering IA: Gemma falhou | %s", exc)
            erros.append(f"Gemma: {exc}")
    raise LetteringModelError("Nenhum provedor textual respondeu: " + " | ".join(erros))
