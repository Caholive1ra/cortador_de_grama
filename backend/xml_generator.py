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
) -> str:
    """Cria de uma a quatro tracks sincronizadas e uma track de áudio PGM."""
    try:
        _validar_entrada(segmentos, fontes, duracao_total)
        logger.info("Iniciando geração do FCP XML em: %s", output_path)

        raiz = ET.Element("xmeml", version="4")
        sequencia = ET.SubElement(raiz, "sequence")
        ET.SubElement(sequencia, "name").text = "Aula_Decupada_Multicamera"
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
        trilha_audio = ET.SubElement(audio, "track")

        for indice_segmento, segmento in enumerate(segmentos):
            frame_start = seconds_to_frames(float(segmento["start"]), fps)
            frame_end = seconds_to_frames(float(segmento["end"]), fps)
            if frame_end <= frame_start:
                continue
            habilitado = bool(
                segmento.get("enabled", segmento.get("track") != "V1")
            )
            for indice_fonte, (fonte, trilha) in enumerate(
                zip(fontes, trilhas_video), start=1
            ):
                clip = _adicionar_clipitem(
                    trilha,
                    f"video_{indice_fonte}_{indice_segmento}",
                    fonte["name"],
                    frame_start,
                    frame_end,
                    "video",
                    fps,
                    habilitado,
                )
                _adicionar_arquivo(
                    clip,
                    f"file_{indice_fonte}",
                    fonte,
                    fps,
                    declarar=indice_segmento == 0,
                )

            clip_audio = _adicionar_clipitem(
                trilha_audio,
                f"audio_1_{indice_segmento}",
                fontes[0]["name"],
                frame_start,
                frame_end,
                "audio",
                fps,
                habilitado,
                source_track_index=1,
            )
            _adicionar_arquivo(clip_audio, "file_1", fontes[0], fps, declarar=False)

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
    if not 1 <= len(fontes) <= 4:
        raise ValueError("O XML exige entre uma e quatro fontes.")
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
) -> ET.Element:
    clipitem = ET.SubElement(trilha, "clipitem", id=clip_id)
    ET.SubElement(clipitem, "name").text = nome
    ET.SubElement(clipitem, "duration").text = str(frame_end - frame_start)
    ET.SubElement(clipitem, "enabled").text = "TRUE" if enabled else "FALSE"
    ET.SubElement(clipitem, "start").text = str(frame_start)
    ET.SubElement(clipitem, "end").text = str(frame_end)
    ET.SubElement(clipitem, "in").text = str(frame_start)
    ET.SubElement(clipitem, "out").text = str(frame_end)
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
