"""Geração de FCP XML (xmeml v4) para pancake editing no Premiere."""

import logging
import os
import traceback
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

XMEML_VERSION = "4"


def seconds_to_frames(seconds: float, fps: int = 30) -> int:
    """Converte segundos decimais em frames inteiros."""
    return int(round(seconds * fps))


def gerar_fcp_xml(
    segmentos: list[dict],
    original_file_path: str,
    output_path: str,
    fps: int = 30,
) -> str:
    """Monta um FCP XML com duas trilhas (V1 erros, V2 fala limpa) e grava em disco.

    Returns:
        Caminho absoluto do arquivo XML gerado.
    """
    try:
        logger.info("Iniciando geração do FCP XML em: %s", output_path)

        clips_v1: list[dict] = []
        clips_v2: list[dict] = []
        for segmento in segmentos:
            clip = {
                "start": seconds_to_frames(float(segmento["start"]), fps),
                "end": seconds_to_frames(float(segmento["end"]), fps),
                "text": str(segmento.get("text", "")),
                "track": str(segmento["track"]),
            }
            if clip["track"] == "V1":
                clips_v1.append(clip)
            else:
                clips_v2.append(clip)

        logger.info(
            "Clipes no XML: V1=%s, V2=%s.",
            len(clips_v1),
            len(clips_v2),
        )

        raiz = _montar_xmeml(
            clips_v1=clips_v1,
            clips_v2=clips_v2,
            original_file_path=original_file_path,
            fps=fps,
        )
        ET.indent(raiz, space="  ")
        xml_corpo = ET.tostring(raiz, encoding="unicode")
        xml_documento = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<!DOCTYPE xmeml>\n"
            f"{xml_corpo}"
        )

        diretorio = os.path.dirname(os.path.abspath(output_path))
        if diretorio:
            os.makedirs(diretorio, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as arquivo:
            arquivo.write(xml_documento)

        caminho_absoluto = os.path.abspath(output_path)
        logger.info("FCP XML gerado com sucesso: %s", caminho_absoluto)
        return caminho_absoluto
    except Exception:
        logger.error(
            "Falha ao gerar o FCP XML em %s.\n%s",
            output_path,
            traceback.format_exc(),
        )
        raise


def _montar_xmeml(
    clips_v1: list[dict],
    clips_v2: list[dict],
    original_file_path: str,
    fps: int,
) -> ET.Element:
    """Constrói a árvore ``xmeml`` version 4 com duas tracks de vídeo."""
    duracao = _duracao_sequencia(clips_v1, clips_v2)
    nome_arquivo = os.path.basename(original_file_path)
    pathurl = _para_pathurl(original_file_path)
    file_id = "file-master"

    raiz = ET.Element("xmeml", version=XMEML_VERSION)
    sequencia = ET.SubElement(raiz, "sequence", id="sequence-1")
    ET.SubElement(sequencia, "name").text = f"Pancake - {nome_arquivo}"
    ET.SubElement(sequencia, "duration").text = str(duracao)
    _adicionar_rate(sequencia, fps)

    media = ET.SubElement(sequencia, "media")
    video = ET.SubElement(media, "video")
    formato = ET.SubElement(video, "format")
    caracteristicas = ET.SubElement(formato, "samplecharacteristics")
    _adicionar_rate(caracteristicas, fps)

    trilha_v1 = ET.SubElement(video, "track")
    trilha_v2 = ET.SubElement(video, "track")

    file_definido = False
    file_definido = _preencher_track(
        trilha=trilha_v1,
        clips=clips_v1,
        fps=fps,
        file_id=file_id,
        pathurl=pathurl,
        nome_arquivo=nome_arquivo,
        prefixo_id="v1",
        file_definido=file_definido,
    )
    _preencher_track(
        trilha=trilha_v2,
        clips=clips_v2,
        fps=fps,
        file_id=file_id,
        pathurl=pathurl,
        nome_arquivo=nome_arquivo,
        prefixo_id="v2",
        file_definido=file_definido,
    )
    return raiz


def _preencher_track(
    trilha: ET.Element,
    clips: list[dict],
    fps: int,
    file_id: str,
    pathurl: str,
    nome_arquivo: str,
    prefixo_id: str,
    file_definido: bool,
) -> bool:
    """Insere clipitems em uma track e devolve se a tag ``<file>`` já foi definida."""
    for indice, clip in enumerate(clips, start=1):
        clipitem = ET.SubElement(trilha, "clipitem", id=f"clip-{prefixo_id}-{indice}")
        ET.SubElement(clipitem, "name").text = clip["text"] or nome_arquivo
        ET.SubElement(clipitem, "enabled").text = "TRUE"
        duracao_clip = max(clip["end"] - clip["start"], 0)
        ET.SubElement(clipitem, "duration").text = str(duracao_clip)
        _adicionar_rate(clipitem, fps)
        ET.SubElement(clipitem, "start").text = str(clip["start"])
        ET.SubElement(clipitem, "end").text = str(clip["end"])
        ET.SubElement(clipitem, "in").text = str(clip["start"])
        ET.SubElement(clipitem, "out").text = str(clip["end"])

        if not file_definido:
            arquivo = ET.SubElement(clipitem, "file", id=file_id)
            ET.SubElement(arquivo, "name").text = nome_arquivo
            ET.SubElement(arquivo, "pathurl").text = pathurl
            _adicionar_rate(arquivo, fps)
            file_definido = True
        else:
            ET.SubElement(clipitem, "file", id=file_id)

    return file_definido


def _adicionar_rate(parent: ET.Element, fps: int) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"


def _duracao_sequencia(clips_v1: list[dict], clips_v2: list[dict]) -> int:
    finais = [clip["end"] for clip in clips_v1 + clips_v2]
    return max(finais) if finais else 0


def _para_pathurl(file_path: str) -> str:
    """Converte um path local no formato ``file://localhost/...`` do FCP XML."""
    absoluto = os.path.abspath(file_path).replace("\\", "/")
    if os.name == "nt":
        return f"file://localhost/{absoluto}"
    if not absoluto.startswith("/"):
        absoluto = f"/{absoluto}"
    return f"file://localhost{absoluto}"
