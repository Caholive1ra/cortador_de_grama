"""API local do Assistente de Decapagem (Pancake Editing)."""

import logging
import os
import traceback

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from logic_engine import classificar_segmentos
from media_utils import extrair_audio_temporario, obter_metadados
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("CORS liberado para o painel UXP em localhost.")


class MediaRequest(BaseModel):
    """Fontes sincronizadas; somente o PGM é obrigatório."""

    pgm_path: str
    camera_1_path: str | None = None
    camera_2_path: str | None = None
    ppt_path: str | None = None


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
def process_media(request: MediaRequest) -> dict[str, str]:
    """Transcreve o PGM e prepara as fontes para o XML multicâmera."""
    try:
        fontes = {
            "PGM": request.pgm_path,
            "Câmera 1": request.camera_1_path,
            "Câmera 2": request.camera_2_path,
            "PPT/Tela": request.ppt_path,
        }
        logger.info(
            "Processamento solicitado. Fontes: %s",
            {nome: caminho for nome, caminho in fontes.items() if caminho},
        )
        metadados = []
        for nome, caminho in fontes.items():
            if not caminho:
                continue
            dados = obter_metadados(caminho)
            dados["label"] = nome
            dados["name"] = os.path.basename(caminho)
            metadados.append(dados)

        pgm = metadados[0]
        tolerancia = 1.0 / float(pgm["fps"])
        for fonte in metadados[1:]:
            if abs(float(fonte["fps"]) - float(pgm["fps"])) > 0.02:
                raise ValueError(
                    f"FPS incompatível entre PGM e {fonte['name']}: "
                    f"{pgm['fps']:.3f} vs {fonte['fps']:.3f}"
                )
            if float(fonte["duration"]) + tolerancia < float(pgm["duration"]):
                raise ValueError(f"{fonte['name']} termina antes do PGM.")

        with extrair_audio_temporario(request.pgm_path) as audio_path:
            segmentos = transcrever_audio(audio_path)
        segmentos_classificados = classificar_segmentos(
            segmentos, duracao_total=float(pgm["duration"])
        )

        raiz_arquivo, _extensao = os.path.splitext(request.pgm_path)
        xml_output_path = f"{raiz_arquivo}_cortado.xml"

        xml_gerado = gerar_fcp_xml(
            segmentos_classificados,
            metadados,
            xml_output_path,
            fps=float(pgm["fps"]),
            duracao_total=float(pgm["duration"]),
        )
        logger.info("Esteira concluída. XML gerado em: %s", xml_gerado)
        return {"status": "success", "xml_path": xml_gerado}
    except FileNotFoundError as exc:
        logger.error(
            "Áudio não encontrado: %s\n%s",
            request.pgm_path,
            traceback.format_exc(),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Falha no processamento: %s\n%s",
            request.pgm_path,
            traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor na porta 8000.")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
