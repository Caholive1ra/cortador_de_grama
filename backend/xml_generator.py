"""Geração de FCP XML multicâmera, contínuo e não destrutivo."""

import logging
import os
import traceback
import xml.etree.ElementTree as ET
from urllib.parse import quote

logger = logging.getLogger(__name__)

CABECALHO_XMEML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<!DOCTYPE xmeml>\n"
    '<xmeml version="4">'
)


def seconds_to_frames(seconds: float, fps: float = 30.0) -> int:
    """Converte segundos em frames usando o frame rate real da fonte."""
    return int(round(seconds * fps))


def gerar_fcp_xml(
    segmentos: list[dict],
    fontes: list[dict],
    output_path: str,
    fps: float,
    duracao_total: float,
    fonte_audio: dict | None = None,
    fontes_audio: list[dict] | None = None,
    camera_por_segmento: list[str] | None = None,
    lettering_suggestions: list[dict] | None = None,
    sequence_name: str = "Aula_Decupada_Multicamera",
) -> str:
    """Cria tracks de vídeo sincronizadas e uma track de áudio final."""
    try:
        _validar_entrada(segmentos, fontes, duracao_total)
        logger.info("Iniciando geração do FCP XML em: %s", output_path)

        raiz = ET.Element("xmeml", version="4")
        sequencia = ET.SubElement(raiz, "sequence")
        ET.SubElement(sequencia, "name").text = sequence_name
        ET.SubElement(sequencia, "duration").text = str(
            seconds_to_frames(duracao_total, fps)
        )
        _adicionar_rate(sequencia, fps)

        media = ET.SubElement(sequencia, "media")
        video = ET.SubElement(media, "video")
        _adicionar_format_video(video, fps, fontes[0]["width"], fontes[0]["height"])
        trilhas_video = [ET.SubElement(video, "track") for _fonte in fontes]

        audio = ET.SubElement(media, "audio")
        _adicionar_format_audio(audio)
        fontes_audio_finais = fontes_audio or [fonte_audio or fontes[0]]
        trilhas_audio = [ET.SubElement(audio, "track") for _ in fontes_audio_finais]
        arquivos_declarados: set[int] = set()
        arquivos_audio_declarados: set[int] = set()

        # A analise usa microsegmentos para localizar cortes com precisao.
        # No XML, unimos trechos adjacentes iguais para evitar emendas visuais
        # que nao representam um corte ou uma troca de camera no Premiere.
        segmentos_xml, cameras_xml = _compactar_segmentos(segmentos, camera_por_segmento)

        for indice_segmento, segmento in enumerate(segmentos_xml):
            frame_start = seconds_to_frames(float(segmento["start"]), fps)
            frame_end = seconds_to_frames(float(segmento["end"]), fps)
            if frame_end <= frame_start:
                continue
            habilitado = bool(
                segmento.get("enabled", segmento.get("track") != "V1")
            )
            camera_ativa = (
                cameras_xml[indice_segmento]
                if cameras_xml and indice_segmento < len(cameras_xml)
                else None
            )
            for indice_fonte, (fonte, trilha) in enumerate(
                zip(fontes, trilhas_video), start=1
            ):
                # A timeline usa o FPS do PGM; in/out usam o FPS nativo da fonte.
                offset_segundos = float(fonte.get("offset_seconds", 0.0))
                inicio_segundos = max(float(segmento["start"]), offset_segundos, 0.0)
                fim_segundos = min(
                    float(segmento["end"]),
                    offset_segundos + float(fonte["duration"]),
                )
                if fim_segundos <= inicio_segundos:
                    continue
                inicio = seconds_to_frames(inicio_segundos, fps)
                fim = seconds_to_frames(fim_segundos, fps)
                fps_fonte = float(fonte.get("fps", fps))
                inicio_fonte = seconds_to_frames(inicio_segundos - offset_segundos, fps_fonte)
                fim_fonte = seconds_to_frames(fim_segundos - offset_segundos, fps_fonte)
                clip = _adicionar_clipitem(
                    trilha,
                    f"video_{indice_fonte}_{indice_segmento}",
                    fonte["name"],
                    inicio,
                    fim,
                    "video",
                    fps,
                    habilitado and (camera_ativa is None or fonte.get("label") == camera_ativa),
                    source_start=inicio_fonte,
                    source_end=fim_fonte,
                )
                _adicionar_arquivo(
                    clip,
                    f"file_{indice_fonte}",
                    fonte,
                    fps_fonte,
                    declarar=indice_fonte not in arquivos_declarados,
                )
                arquivos_declarados.add(indice_fonte)

            for indice_audio, (fonte_audio_final, trilha_audio) in enumerate(
                zip(fontes_audio_finais, trilhas_audio), start=1
            ):
                habilitado_audio = habilitado and bool(
                    fonte_audio_final.get("enabled_by_default", True)
                )
                offset_audio = seconds_to_frames(
                    float(fonte_audio_final.get("offset_seconds", 0.0)), fps
                )
                duracao_audio = seconds_to_frames(float(fonte_audio_final["duration"]), fps)
                inicio_audio = max(frame_start, offset_audio, 0)
                fim_audio = min(frame_end, offset_audio + duracao_audio)
                if fim_audio <= inicio_audio:
                    continue
                clip_audio = _adicionar_clipitem(
                    trilha_audio,
                    f"audio_{indice_audio}_{indice_segmento}",
                    fonte_audio_final["name"],
                    inicio_audio,
                    fim_audio,
                    "audio",
                    fps,
                    habilitado_audio,
                    source_track_index=1,
                    source_start=inicio_audio - offset_audio,
                )
                _adicionar_arquivo(
                    clip_audio,
                    f"file_audio_{indice_audio}",
                    fonte_audio_final,
                    fps,
                    declarar=indice_audio not in arquivos_audio_declarados,
                )
                arquivos_audio_declarados.add(indice_audio)

        _adicionar_marcadores_revisao(sequencia, segmentos, fps)
        _adicionar_marcadores_lettering(sequencia, lettering_suggestions or [], fps)

        xml_documento = _serializar_xmeml(raiz)
        diretorio = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(diretorio, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="\n") as arquivo_xml:
            arquivo_xml.write(xml_documento)

        caminho_absoluto = os.path.abspath(output_path)
        logger.info("FCP XML multicâmera gerado: %s", caminho_absoluto)
        return caminho_absoluto
    except Exception:
        logger.error("Falha ao gerar XML em %s.\n%s", output_path, traceback.format_exc())
        raise


def _validar_entrada(segmentos: list[dict], fontes: list[dict], duracao: float) -> None:
    if not 1 <= len(fontes) <= 6:
        raise ValueError("O XML exige entre uma e seis fontes de vídeo.")
    if duracao <= 0 or not segmentos:
        raise ValueError("A timeline precisa possuir duração e segmentos.")
    cursor = 0.0
    for segmento in segmentos:
        inicio = float(segmento["start"])
        fim = float(segmento["end"])
        if abs(inicio - cursor) > 0.001 or fim <= inicio:
            raise ValueError("Segmentos não formam uma timeline contínua.")
        cursor = fim
    if abs(cursor - duracao) > 0.001:
        raise ValueError("A timeline não termina na duração total da mídia.")


def _compactar_segmentos(
    segmentos: list[dict], camera_por_segmento: list[str] | None,
) -> tuple[list[dict], list[str] | None]:
    """Une microsegmentos sem esconder descartes ou trocas de camera."""
    compactados: list[dict] = []
    cameras_compactadas: list[str] | None = [] if camera_por_segmento else None

    for indice, segmento in enumerate(segmentos):
        camera = (
            camera_por_segmento[indice]
            if camera_por_segmento and indice < len(camera_por_segmento)
            else None
        )
        habilitado = bool(segmento.get("enabled", segmento.get("track") != "V1"))
        anterior = compactados[-1] if compactados else None
        camera_anterior = cameras_compactadas[-1] if cameras_compactadas else None
        if (
            anterior is not None
            and abs(float(anterior["end"]) - float(segmento["start"])) <= 0.001
            and bool(anterior.get("enabled", anterior.get("track") != "V1")) == habilitado
            and camera_anterior == camera
        ):
            anterior["end"] = segmento["end"]
            continue

        compactados.append(dict(segmento))
        if cameras_compactadas is not None:
            cameras_compactadas.append(camera)

    return compactados, cameras_compactadas


def _adicionar_marcadores_revisao(
    sequencia: ET.Element, segmentos: list[dict], fps: float,
) -> None:
    """Inclui marcadores para trechos mantidos por duvida editorial da IA."""
    for segmento in segmentos:
        if not segmento.get("review"):
            continue
        marcador = ET.SubElement(sequencia, "marker")
        ET.SubElement(marcador, "name").text = "REVISAR: duvida da IA"
        ET.SubElement(marcador, "comment").text = str(
            segmento.get("review_reason") or "duvida_editorial"
        )
        ET.SubElement(marcador, "in").text = str(
            seconds_to_frames(float(segmento["start"]), fps)
        )
        ET.SubElement(marcador, "out").text = str(
            seconds_to_frames(float(segmento["end"]), fps)
        )


def _adicionar_marcadores_lettering(
    sequencia: ET.Element, sugestoes: list[dict], fps: float,
) -> None:
    """Inclui marcadores editoriais sem criar nem alterar textos na imagem."""
    for sugestao in sugestoes:
        marcador = ET.SubElement(sequencia, "marker")
        ET.SubElement(marcador, "name").text = "LETTERING: " + str(sugestao["text"])
        ET.SubElement(marcador, "comment").text = "Por que: " + str(
            sugestao.get("explanation") or sugestao.get("reason", "conceito")
        )
        ET.SubElement(marcador, "in").text = str(
            seconds_to_frames(float(sugestao["start"]), fps)
        )
        ET.SubElement(marcador, "out").text = str(
            seconds_to_frames(float(sugestao["end"]), fps)
        )


def _serializar_xmeml(raiz: ET.Element) -> str:
    sequencia = raiz.find("sequence")
    if sequencia is None:
        raise ValueError("Árvore XML sem <sequence>.")
    ET.indent(sequencia, space="  ")
    conteudo = ET.tostring(sequencia, encoding="unicode")
    indentado = "\n".join(f"  {linha}" for linha in conteudo.splitlines())
    return f"{CABECALHO_XMEML}\n{indentado}\n</xmeml>\n"


def _rate_values(fps: float) -> tuple[int, bool]:
    nominal = int(round(fps))
    ntsc = abs(fps - nominal * 1000 / 1001) < 0.02
    return nominal, ntsc


def _adicionar_rate(parent: ET.Element, fps: float) -> None:
    nominal, ntsc = _rate_values(fps)
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(nominal)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _adicionar_format_video(
    video: ET.Element, fps: float, width: int, height: int
) -> None:
    formato = ET.SubElement(video, "format")
    caracteristicas = ET.SubElement(formato, "samplecharacteristics")
    _adicionar_rate(caracteristicas, fps)
    ET.SubElement(caracteristicas, "width").text = str(width)
    ET.SubElement(caracteristicas, "height").text = str(height)
    ET.SubElement(caracteristicas, "pixelaspectratio").text = "square"


def _adicionar_format_audio(audio: ET.Element) -> None:
    formato = ET.SubElement(audio, "format")
    caracteristicas = ET.SubElement(formato, "samplecharacteristics")
    ET.SubElement(caracteristicas, "depth").text = "16"
    ET.SubElement(caracteristicas, "samplerate").text = "48000"


def _adicionar_clipitem(
    trilha: ET.Element,
    clip_id: str,
    nome: str,
    frame_start: int,
    frame_end: int,
    media_type: str,
    fps: float,
    enabled: bool,
    source_track_index: int | None = None,
    source_start: int | None = None,
    source_end: int | None = None,
) -> ET.Element:
    clipitem = ET.SubElement(trilha, "clipitem", id=clip_id)
    ET.SubElement(clipitem, "name").text = nome
    ET.SubElement(clipitem, "duration").text = str(frame_end - frame_start)
    ET.SubElement(clipitem, "enabled").text = "TRUE" if enabled else "FALSE"
    ET.SubElement(clipitem, "start").text = str(frame_start)
    ET.SubElement(clipitem, "end").text = str(frame_end)
    entrada = frame_start if source_start is None else source_start
    ET.SubElement(clipitem, "in").text = str(entrada)
    saida = entrada + frame_end - frame_start if source_end is None else source_end
    ET.SubElement(clipitem, "out").text = str(saida)
    _adicionar_rate(clipitem, fps)
    sourcetrack = ET.SubElement(clipitem, "sourcetrack")
    ET.SubElement(sourcetrack, "mediatype").text = media_type
    if source_track_index is not None:
        ET.SubElement(sourcetrack, "trackindex").text = str(source_track_index)
    return clipitem


def _adicionar_arquivo(
    clipitem: ET.Element,
    file_id: str,
    fonte: dict,
    fps: float,
    declarar: bool,
) -> None:
    arquivo = ET.SubElement(clipitem, "file", id=file_id)
    if not declarar:
        return
    ET.SubElement(arquivo, "name").text = fonte["name"]
    ET.SubElement(arquivo, "pathurl").text = _criar_pathurl(fonte["path"])
    _adicionar_rate(arquivo, fps)
    ET.SubElement(arquivo, "duration").text = str(
        seconds_to_frames(float(fonte["duration"]), fps)
    )
    media = ET.SubElement(arquivo, "media")
    if fonte.get("kind") != "audio":
        video = ET.SubElement(media, "video")
        caracteristicas = ET.SubElement(video, "samplecharacteristics")
        _adicionar_rate(caracteristicas, fps)
        ET.SubElement(caracteristicas, "width").text = str(fonte["width"])
        ET.SubElement(caracteristicas, "height").text = str(fonte["height"])
        ET.SubElement(caracteristicas, "pixelaspectratio").text = "square"
    audio = ET.SubElement(media, "audio")
    ET.SubElement(audio, "channelcount").text = "2"


def _criar_pathurl(file_path: str) -> str:
    caminho = os.path.abspath(file_path).replace("\\", "/")
    return f"file://localhost/{quote(caminho, safe='/:')}"
