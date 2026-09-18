"""API local do Assistente de Decapagem (Pancake Editing)."""

import logging
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from transcriber import transcrever_audio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Assistente de Decapagem",
    description="Backend local para pré-edição de aulas (Pancake Editing).",
    version="0.1.0",
)


class AudioRequest(BaseModel):
    """Payload com o caminho absoluto do áudio bruto."""

    file_path: str


@app.get("/health")
def health_check() -> dict[str, str]:
    """Confirma que o servidor local está no ar."""
    try:
        logger.info("Health-check solicitado.")
        return {"status": "ok"}
    except Exception:
        logger.error("Falha no health-check.\n%s", traceback.format_exc())
        raise


@app.post("/process")
def process_audio(payload: AudioRequest) -> list[dict]:
    """Transcreve o áudio informado e devolve os segmentos com timestamps."""
    try:
        logger.info("Processamento solicitado para: %s", payload.file_path)
        segmentos = transcrever_audio(payload.file_path)
        logger.info("Processamento concluído. Segmentos: %s", len(segmentos))
        return segmentos
    except FileNotFoundError as exc:
        logger.error(
            "Áudio não encontrado: %s\n%s",
            payload.file_path,
            traceback.format_exc(),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Falha no processamento: %s\n%s",
            payload.file_path,
            traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor na porta 8000.")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
