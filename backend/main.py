"""API local do Assistente de Decapagem (Pancake Editing)."""

import logging
import os
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from logic_engine import classificar_segmentos
from transcriber import transcrever_audio
from xml_generator import gerar_fcp_xml

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
def process_audio(request: AudioRequest) -> dict[str, str]:
    """Transcreve, classifica e gera o FCP XML de pancake editing."""
    try:
        logger.info("Processamento solicitado para: %s", request.file_path)
        segmentos = transcrever_audio(request.file_path)
        segmentos_classificados = classificar_segmentos(segmentos)

        raiz_arquivo, _extensao = os.path.splitext(request.file_path)
        xml_output_path = f"{raiz_arquivo}_cortado.xml"

        xml_gerado = gerar_fcp_xml(
            segmentos_classificados,
            request.file_path,
            xml_output_path,
        )
        logger.info("Esteira concluída. XML gerado em: %s", xml_gerado)
        return {"status": "success", "xml_path": xml_output_path}
    except FileNotFoundError as exc:
        logger.error(
            "Áudio não encontrado: %s\n%s",
            request.file_path,
            traceback.format_exc(),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Falha no processamento: %s\n%s",
            request.file_path,
            traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor na porta 8000.")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
