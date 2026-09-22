"""API local do Assistente de Decapagem (Pancake Editing)."""

import logging
import os
import json
import traceback
from contextlib import ExitStack

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from audio_sync import SyncError, sincronizar_audio
from camera_director import dirigir_cameras
from editorial_ai import EditorialModelError, obter_diagnostico_editorial
from logic_engine import classificar_segmentos
from media_utils import extrair_audio_temporario, obter_metadados, obter_metadados_audio
from transcriber import ModelUnavailableError, transcrever_audio
from xml_generator import gerar_fcp_xml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
BACKEND_REVISION = "diretor-cameras-audio-v15"

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
    """Fontes com audio comum; somente o PGM e obrigatorio."""

    pgm_path: str
    camera_1_path: str | None = None
    camera_2_path: str | None = None
    camera_3_path: str | None = None
    ppt_path: str | None = None
    audio_path: str | None = None
    project_type: str = "videoaula"
    participante_1_path: str | None = None
    participante_2_path: str | None = None
    participante_3_path: str | None = None
    participante_4_path: str | None = None
    camera_geral_path: str | None = None
    audio_participante_1_path: str | None = None
    audio_participante_2_path: str | None = None
    audio_participante_3_path: str | None = None
    audio_participante_4_path: str | None = None
    audio_camera_geral_path: str | None = None


@app.get("/health")
def health_check() -> dict[str, str]:
    """Confirma que o servidor local está no ar."""
    try:
        logger.info("Health-check solicitado.")
        return {"status": "ok", "revision": BACKEND_REVISION}
    except Exception:
        logger.error("Falha no health-check.\n%s", traceback.format_exc())
        raise


@app.post("/process")
def process_media(request: MediaRequest) -> dict:
    """Transcreve o PGM e prepara as fontes para o XML multicâmera."""
    try:
        if request.project_type not in {"videoaula", "videocast"}:
            raise ValueError("Tipo de projeto inválido. Use videoaula ou videocast.")
        fontes = (
            {
                "PGM": request.pgm_path,
                "Participante 1": request.participante_1_path,
                "Participante 2": request.participante_2_path,
                "Participante 3": request.participante_3_path,
                "Participante 4": request.participante_4_path,
                "Câmera geral": request.camera_geral_path,
            }
            if request.project_type == "videocast"
            else {
                "PGM": request.pgm_path,
                "Câmera 1": request.camera_1_path,
                "Câmera 2": request.camera_2_path,
                "PPT/Tela": request.ppt_path,
            }
        )
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
        pgm["offset_seconds"] = 0.0
        for fonte in metadados[1:]:
            if abs(float(fonte["fps"]) - float(pgm["fps"])) > 0.02:
                logger.info(
                    "Fonte %s usa %.3f fps; a timeline permanece em %.3f fps.",
                    fonte["name"], fonte["fps"], pgm["fps"],
                )

        sincronizacao = []
        with extrair_audio_temporario(request.pgm_path, pgm.get("video_start", 0.0)) as audio_path:
            for fonte in metadados[1:]:
                try:
                    with extrair_audio_temporario(fonte["path"], fonte.get("video_start", 0.0)) as fonte_audio:
                        ajuste = sincronizar_audio(audio_path, fonte_audio, float(pgm["fps"]))
                except SyncError as exc:
                    raise SyncError(f"{fonte['label']} ({fonte['name']}): {exc}") from exc
                fonte["offset_seconds"] = ajuste.offset_seconds
                inicio = max(0.0, ajuste.offset_seconds)
                fim = min(float(pgm["duration"]), ajuste.offset_seconds + float(fonte["duration"]))
                if fim <= inicio:
                    raise SyncError(f"{fonte['name']}: o video nao cobre nenhum trecho do PGM.")
                sincronizacao.append({
                    "source": fonte["label"], **ajuste.to_dict(),
                    "coverage_start": inicio, "coverage_end": fim,
                })
            entradas_audio = [("Áudio master", request.audio_path, None)]
            if request.project_type == "videocast":
                entradas_audio.extend([
                    ("Áudio participante 1", request.audio_participante_1_path, "Participante 1"),
                    ("Áudio participante 2", request.audio_participante_2_path, "Participante 2"),
                    ("Áudio participante 3", request.audio_participante_3_path, "Participante 3"),
                    ("Áudio participante 4", request.audio_participante_4_path, "Participante 4"),
                    ("Áudio câmera geral", request.audio_camera_geral_path, "Câmera geral"),
                ])
            audios_externos = []
            for rotulo, caminho_audio, camera_associada in entradas_audio:
                if not caminho_audio:
                    continue
                fonte_audio = obter_metadados_audio(caminho_audio)
                fonte_audio["label"] = rotulo
                fonte_audio["name"] = os.path.basename(caminho_audio)
                fonte_audio["camera_label"] = camera_associada
                with extrair_audio_temporario(
                    fonte_audio["path"], fonte_audio.get("audio_start", 0.0)
                ) as audio_externo:
                    try:
                        ajuste = sincronizar_audio(audio_path, audio_externo, float(pgm["fps"]))
                    except SyncError as exc:
                        raise SyncError(
                            f"{rotulo} ({fonte_audio['name']}): {exc}"
                        ) from exc
                fonte_audio["offset_seconds"] = ajuste.offset_seconds
                inicio = max(0.0, ajuste.offset_seconds)
                fim = min(float(pgm["duration"]), ajuste.offset_seconds + float(fonte_audio["duration"]))
                if fim <= inicio:
                    raise SyncError("Áudio externo não cobre nenhum trecho do PGM.")
                sincronizacao.append({
                    "source": fonte_audio["label"], **ajuste.to_dict(),
                    "coverage_start": inicio, "coverage_end": fim,
                })
                audios_externos.append(fonte_audio)
            segmentos = transcrever_audio(audio_path)
            segmentos_classificados = classificar_segmentos(
                segmentos,
                duracao_total=float(pgm["duration"]),
                revisao_semantica=True,
                audio_path=audio_path,
            )
            camera_por_segmento = None
            if request.project_type == "videocast":
                with ExitStack() as arquivos_temporarios:
                    fontes_direcao = []
                    for fonte in audios_externos:
                        if fonte.get("camera_label") in (None, "Câmera geral"):
                            continue
                        wav = arquivos_temporarios.enter_context(
                            extrair_audio_temporario(
                                fonte["path"], fonte.get("audio_start", 0.0)
                            )
                        )
                        fontes_direcao.append((
                            fonte["camera_label"], wav, float(fonte["offset_seconds"])
                        ))
                    camera_geral = (
                        "Câmera geral" if request.camera_geral_path else "PGM"
                    )
                    camera_por_segmento = dirigir_cameras(
                        segmentos_classificados, fontes_direcao, camera_geral
                    )

        raiz_arquivo, _extensao = os.path.splitext(request.pgm_path)
        xml_output_path = f"{raiz_arquivo}_cortado.xml"

        xml_gerado = gerar_fcp_xml(
            segmentos_classificados,
            metadados,
            xml_output_path,
            fps=float(pgm["fps"]),
            duracao_total=float(pgm["duration"]),
            fontes_audio=_preparar_fontes_audio_xml(
                pgm, audios_externos, bool(request.audio_path)
            ),
            camera_por_segmento=camera_por_segmento,
        )
        diagnostico_path = f"{raiz_arquivo}_diagnostico.json"
        with open(diagnostico_path, "w", encoding="utf-8") as diagnostico:
            json.dump(
                {
                    "source": request.pgm_path,
                    "editorial_review": obter_diagnostico_editorial(),
                    "segmentos": segmentos_classificados,
                },
                diagnostico, ensure_ascii=False, indent=2,
            )
        logger.info("Esteira concluída. XML gerado em: %s", xml_gerado)
        return {
            "status": "success", "xml_path": xml_gerado,
            "diagnostic_path": os.path.abspath(diagnostico_path),
            "synchronization": sincronizacao,
        }
    except EditorialModelError as exc:
        logger.error("Revisor editorial indisponivel: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelUnavailableError as exc:
        logger.error("Modelo de transcricao indisponivel: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("Midias invalidas: %s", exc, exc_info=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


def _preparar_fontes_audio_xml(
    pgm: dict, audios_externos: list[dict], tem_audio_master: bool
) -> list[dict]:
    """Deixa apenas o master ativo; áudios de câmera ficam como alternativas."""
    if tem_audio_master:
        master = dict(audios_externos[0])
        master["enabled_by_default"] = True
        alternativas = [dict(fonte, enabled_by_default=False) for fonte in audios_externos[1:]]
    else:
        master = dict(pgm)
        master["enabled_by_default"] = True
        alternativas = [dict(fonte, enabled_by_default=False) for fonte in audios_externos]
    fontes = [master, *alternativas]
    logger.info(
        "Áudios incluídos no XML: %s (ativo: %s)",
        [fonte["name"] for fonte in fontes], master["name"],
    )
    return fontes


if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor na porta 8000.")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
