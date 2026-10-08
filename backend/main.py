"""API local do Assistente de Decapagem (Pancake Editing)."""

import logging
import os
import json
import traceback
from time import perf_counter
from uuid import uuid4
from contextlib import ExitStack

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from audio_sync import SyncError, sincronizar_audio
from camera_director import dirigir_cameras
from editorial_ai import (
    GEMINI_API_KEY, GEMINI_MODEL, NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_MODEL, OLLAMA_MODEL, EditorialModelError,
    obter_diagnostico_editorial, obter_ultimo_modelo_textual,
    _modelos_gemini, _request_url, _detalhe_erro_remoto, _erro_nvidia_autenticacao,
    _erro_nvidia_bloqueio_rede,
)
from lettering_ai import LetteringModelError, sugerir_letterings
from learning_feedback import comparar_xmls, salvar_feedback
from learning_bank import listar_banco, ler_registro, anotar, definir_papel, avaliar
from logic_engine import classificar_segmentos
from media_utils import extrair_audio_temporario, obter_metadados, obter_metadados_audio
from transcriber import ModelUnavailableError, transcrever_audio
from xml_generator import gerar_fcp_xml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
BACKEND_REVISION = "retake-pairs-v21"
LIMITE_AULA_MODO_COMPLETO_SEGUNDOS = 3600.0
LIMITE_AUTO_MODO_ECONOMICO_SEGUNDOS = 1200.0

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
    editorial_review: bool = False
    editorial_mode: str = "full"
    skip_unsynced_sources: bool = False
    audio_follows_camera: bool = False


class LetteringRequest(BaseModel):
    """Arquivo exportado apos a revisao humana da sequencia."""

    revised_media_path: str
    economic_mode: bool = False


class LetteringSegmentsRequest(BaseModel):
    """Transcricao/timestamps exportados da sequencia ativa do Premiere."""

    segments: list[dict]
    economic_mode: bool = False


class LearningFeedbackRequest(BaseModel):
    """XML original da IA e XML da sequencia final exportada pelo editor."""

    suggested_xml_path: str
    revised_xml_path: str
    project_type: str | None = None
    diagnostic_path: str | None = None


class LearningAnnotationRequest(BaseModel):
    example_ids: list[str]
    status: str
    reason: str | None = None
    note: str = ""


class LearningRoleRequest(BaseModel):
    role: str


class LearningEvaluationRequest(BaseModel):
    candidate_xml: str


class EconomicModeRequiredError(ValueError):
    """Aula longa precisa de revisao economica para evitar prompt excessivo."""

    def __init__(self, duration_seconds: float):
        self.duration_seconds = duration_seconds
        super().__init__("Aulas acima de uma hora exigem o modo economico para revisao por IA.")


@app.get("/health")
def health_check() -> dict[str, str]:
    """Confirma que o servidor local está no ar."""
    try:
        logger.info("Health-check solicitado.")
        return {
            "status": "ok",
            "revision": BACKEND_REVISION,
            "gemini_model": GEMINI_MODEL,
            "gemini_configured": "yes" if GEMINI_API_KEY else "no",
            "nvidia_model": NVIDIA_MODEL,
            "nvidia_configured": "yes" if NVIDIA_API_KEY else "no",
        }
    except Exception:
        logger.error("Falha no health-check.\n%s", traceback.format_exc())
        raise


@app.get("/diagnostics/gemini")
def diagnostics_gemini() -> dict:
    """Lista modelos visiveis para a chave, sem retornar a chave secreta."""
    if not GEMINI_API_KEY:
        return {"configured": False, "configured_models": list(_modelos_gemini())}
    try:
        resposta = _request_url(
            "GET", f"https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=30,
        )
        disponiveis = [
            item.get("name", "").removeprefix("models/")
            for item in resposta.get("models", [])
            if "generateContent" in item.get("supportedGenerationMethods", [])
        ]
        return {
            "configured": True,
            "configured_models": list(_modelos_gemini()),
            "available_generate_content_models": disponiveis,
            "configured_models_available": {
                modelo: modelo in disponiveis for modelo in _modelos_gemini()
            },
        }
    except Exception as exc:
        logger.error("Diagnostico Gemini falhou: %s", exc, exc_info=True)
        return {
            "configured": True,
            "configured_models": list(_modelos_gemini()),
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
        }


@app.get("/diagnostics/nvidia")
def diagnostics_nvidia() -> dict:
    """Confere a chave NVIDIA e lista modelos, sem retornar a credencial."""
    if not NVIDIA_API_KEY:
        return {"configured": False, "configured_model": NVIDIA_MODEL}
    try:
        resposta = _request_url(
            "GET", f"{NVIDIA_BASE_URL}/models",
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"}, timeout=30,
        )
        disponiveis = [item.get("id") for item in resposta.get("data", []) if item.get("id")]
        return {
            "configured": True,
            "configured_model": NVIDIA_MODEL,
            "configured_model_available": NVIDIA_MODEL in disponiveis,
            "available_models": disponiveis,
        }
    except Exception as exc:
        logger.error("Diagnostico NVIDIA falhou: %s", exc, exc_info=True)
        autenticacao = _erro_nvidia_autenticacao(exc)
        bloqueio_rede = _erro_nvidia_bloqueio_rede(exc)
        return {
            "configured": True, "configured_model": NVIDIA_MODEL,
            "error_type": type(exc).__name__, "error": _detalhe_erro_remoto(exc)[:1000],
            "authentication_failed": autenticacao,
            "network_blocked": bloqueio_rede,
            "action": (
                "Substitua NVIDIA_API_KEY no .env por uma chave valida do console NVIDIA e reinicie o backend."
                if autenticacao else
                "Solicite ao TI a liberacao HTTPS de integrate.api.nvidia.com; a rede devolveu error code 1010."
                if bloqueio_rede else "Verifique os limites e permissoes do projeto NVIDIA."
            ),
        }


@app.post("/process")
def process_media(request: MediaRequest) -> dict:
    """Transcreve o PGM e prepara as fontes para o XML multicâmera."""
    try:
        inicio_processamento = perf_counter()
        desempenho: dict[str, float] = {}

        def marcar_etapa(nome: str) -> None:
            desempenho[nome] = round(perf_counter() - inicio_processamento, 3)
            logger.info("Desempenho: %s concluido em %.3fs.", nome, desempenho[nome])

        if request.editorial_mode not in {"auto", "full", "economic"}:
            raise ValueError("Modo editorial invalido. Use auto, full ou economic.")
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
        marcar_etapa("metadata")

        pgm = metadados[0]
        modo_editorial_efetivo = request.editorial_mode
        if (
            modo_editorial_efetivo == "auto"
            and request.editorial_review
            and float(pgm["duration"]) > LIMITE_AUTO_MODO_ECONOMICO_SEGUNDOS
        ):
            modo_editorial_efetivo = "economic"
            logger.info(
                "Modo economico selecionado automaticamente para aula de %.1f minutos.",
                float(pgm["duration"]) / 60,
            )
        elif modo_editorial_efetivo == "auto":
            modo_editorial_efetivo = "full"
        if (
            request.editorial_review
            and modo_editorial_efetivo == "full"
            and float(pgm["duration"]) > LIMITE_AULA_MODO_COMPLETO_SEGUNDOS
        ):
            raise EconomicModeRequiredError(float(pgm["duration"]))
        pgm["offset_seconds"] = 0.0
        fontes_sincronizadas = [pgm]
        fontes_ignoradas = []
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
                except (SyncError, ValueError) as exc:
                    detalhe = f"{fonte['label']} ({fonte['name']}): {exc}"
                    if not request.skip_unsynced_sources:
                        raise SyncError(detalhe) from exc
                    logger.warning("Fonte opcional ignorada: %s", detalhe)
                    fontes_ignoradas.append({"source": fonte["label"], "reason": str(exc)})
                    continue
                fonte["offset_seconds"] = ajuste.offset_seconds
                inicio = max(0.0, ajuste.offset_seconds)
                fim = min(float(pgm["duration"]), ajuste.offset_seconds + float(fonte["duration"]))
                if fim <= inicio:
                    raise SyncError(f"{fonte['name']}: o video nao cobre nenhum trecho do PGM.")
                sincronizacao.append({
                    "source": fonte["label"], **ajuste.to_dict(),
                    "coverage_start": inicio, "coverage_end": fim,
                })
                fontes_sincronizadas.append(fonte)
            metadados = fontes_sincronizadas
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
            marcar_etapa("audio_sync")
            segmentos = transcrever_audio(audio_path)
            marcar_etapa("transcription")
            segmentos_classificados = classificar_segmentos(
                segmentos,
                duracao_total=float(pgm["duration"]),
                revisao_semantica=request.editorial_review,
                audio_path=audio_path,
                modo_economico=modo_editorial_efetivo == "economic",
            )
            marcar_etapa("editorial_analysis")
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
                    cameras_detectadas = []
                    camera_por_segmento = dirigir_cameras(
                        segmentos_classificados, fontes_direcao, camera_geral,
                        cameras_detectadas=cameras_detectadas,
                    )
                    for segmento, camera, detectada in zip(
                        segmentos_classificados, camera_por_segmento, cameras_detectadas
                    ):
                        segmento["audio_master_required"] = camera != detectada
            marcar_etapa("camera_direction")

        raiz_arquivo, _extensao = os.path.splitext(request.pgm_path)
        job_id = uuid4().hex[:12]
        xml_output_path = f"{raiz_arquivo}_cortado_{job_id}.xml"

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
            audio_follows_camera=request.project_type == "videocast" and request.audio_follows_camera,
        )
        marcar_etapa("xml_generation")
        diagnostico_path = f"{raiz_arquivo}_diagnostico_{job_id}.json"
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
            "editorial_mode_used": modo_editorial_efetivo,
            "performance_seconds": {
                **desempenho, "total": round(perf_counter() - inicio_processamento, 3),
            },
            "review_count": sum(bool(s.get("review")) for s in segmentos_classificados),
            "diagnostic_path": os.path.abspath(diagnostico_path),
            "synchronization": sincronizacao,
            "skipped_sources": fontes_ignoradas,
            "ai_models": {
                "cuts": (
                    "regras locais" if not request.editorial_review
                    else _nome_modelo_cortes(obter_diagnostico_editorial())
                ),
                "letterings": None,
            },
        }
    except EditorialModelError as exc:
        logger.error("Revisor editorial indisponivel: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelUnavailableError as exc:
        logger.error("Modelo de transcricao indisponivel: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EconomicModeRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ECONOMIC_MODE_REQUIRED",
                "duration_seconds": exc.duration_seconds,
                "message": str(exc),
            },
        ) from exc
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


@app.post("/analyze-lettering")
def analyze_lettering(request: LetteringRequest) -> dict:
    """Analisa a versao revisada e gera XML auxiliar com marcadores de texto."""
    try:
        midia = obter_metadados(request.revised_media_path)
        if (
            not request.economic_mode
            and float(midia["duration"]) > LIMITE_AULA_MODO_COMPLETO_SEGUNDOS
        ):
            raise EconomicModeRequiredError(float(midia["duration"]))
        with extrair_audio_temporario(
            request.revised_media_path, midia.get("video_start", 0.0)
        ) as audio_path:
            segmentos = transcrever_audio(audio_path)
        sugestoes = sugerir_letterings(segmentos, modo_economico=request.economic_mode)
        raiz_arquivo, _ = os.path.splitext(request.revised_media_path)
        job_id = uuid4().hex[:12]
        xml_path = f"{raiz_arquivo}_lettering_sugerido_{job_id}.xml"
        diagnostico_path = f"{raiz_arquivo}_lettering_sugerido_{job_id}.json"
        gerar_fcp_xml(
            [{"start": 0.0, "end": float(midia["duration"]), "enabled": True}],
            [dict(midia, name=os.path.basename(request.revised_media_path), label="Video revisado")],
            xml_path,
            fps=float(midia["fps"]),
            duracao_total=float(midia["duration"]),
            lettering_suggestions=sugestoes,
            sequence_name="Aula_Revisada_Sugestoes_Lettering",
        )
        with open(diagnostico_path, "w", encoding="utf-8") as diagnostico:
            json.dump({
                "source": request.revised_media_path,
                "suggestions": sugestoes,
                "transcript": segmentos,
            }, diagnostico, ensure_ascii=False, indent=2)
        return {
            "status": "success",
            "xml_path": os.path.abspath(xml_path),
            "diagnostic_path": os.path.abspath(diagnostico_path),
            "suggestions": sugestoes,
            "ai_models": {
                "cuts": None,
                "letterings": obter_ultimo_modelo_textual() or GEMINI_MODEL,
            },
        }
    except LetteringModelError as exc:
        logger.warning("Analise de lettering indisponivel: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EconomicModeRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "ECONOMIC_MODE_REQUIRED", "duration_seconds": exc.duration_seconds, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Falha na analise de lettering.\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/analyze-lettering-segments")
def analyze_lettering_segments(request: LetteringSegmentsRequest) -> dict:
    """Sugere letterings sem exigir que a sequencia seja um arquivo no disco."""
    try:
        duracao = max((float(item.get("end", 0)) for item in request.segments), default=0.0)
        if not request.economic_mode and duracao > LIMITE_AULA_MODO_COMPLETO_SEGUNDOS:
            raise EconomicModeRequiredError(duracao)
        sugestoes = sugerir_letterings(request.segments, modo_economico=request.economic_mode)
        return {
            "status": "success",
            "suggestions": sugestoes,
            "ai_models": {
                "cuts": None,
                "letterings": obter_ultimo_modelo_textual() or GEMINI_MODEL,
            },
        }
    except LetteringModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EconomicModeRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "ECONOMIC_MODE_REQUIRED", "duration_seconds": exc.duration_seconds, "message": str(exc)},
        ) from exc


@app.post("/learning/compare-xml")
def compare_learning_xml(request: LearningFeedbackRequest) -> dict:
    """Registra a diferenca entre sugestao e revisao humana, localmente."""
    try:
        comparison = comparar_xmls(request.suggested_xml_path, request.revised_xml_path)
        feedback_path, summary = salvar_feedback(
            comparison, request.suggested_xml_path, request.revised_xml_path,
            request.project_type, request.diagnostic_path,
        )
        return {
            "status": "success", "comparison": comparison,
            "feedback_path": feedback_path, "learning_summary": summary,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _learning_call(func, *args) -> dict:
    try:
        return func(*args)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Registro ou XML nao encontrado.") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/learning/records")
def learning_records() -> dict:
    logger.info("Consulta ao banco de feedback.")
    return _learning_call(listar_banco)


@app.get("/learning/records/{record_id}")
def learning_record(record_id: str) -> dict:
    return _learning_call(ler_registro, record_id)


@app.post("/learning/records/{record_id}/annotations")
def learning_annotations(record_id: str, request: LearningAnnotationRequest) -> dict:
    return _learning_call(anotar, record_id, request.example_ids, request.status, request.reason, request.note)


@app.post("/learning/records/{record_id}/role")
def learning_role(record_id: str, request: LearningRoleRequest) -> dict:
    return _learning_call(definir_papel, record_id, request.role)


@app.post("/learning/records/{record_id}/evaluate")
def learning_evaluate(record_id: str, request: LearningEvaluationRequest) -> dict:
    logger.info("Avaliacao comparativa de cortes: %s", record_id)
    return _learning_call(avaliar, record_id, request.candidate_xml)


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


def _nome_modelo_cortes(diagnostico: dict) -> str:
    """Converte o diagnóstico do fallback em nome legível para o painel."""
    modo = str(diagnostico.get("mode", ""))
    if modo.startswith("nvidia"):
        return str(diagnostico.get("model") or NVIDIA_MODEL)
    if modo.startswith("laya"):
        return "Laya local"
    if modo == "ollama":
        return "Ollama/" + OLLAMA_MODEL
    return str(diagnostico.get("gemini_model_used") or diagnostico.get("model") or GEMINI_MODEL)


if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor na porta 8000.")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
