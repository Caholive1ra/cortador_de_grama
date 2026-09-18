"""Geração de FCP XML (xmeml v4) para pancake editing no Premiere."""

import logging
import os
import traceback
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

CABECALHO_XMEML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<!DOCTYPE xmeml>\n"
    '<xmeml version="4">'
)


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

        caminho_video = _caminho_como_mp4(original_file_path)
        pathurl_formatado = "file://localhost/" + caminho_video.replace("\\", "/")
        nome_arquivo = os.path.basename(caminho_video)
        logger.info("pathurl_formatado: %s", pathurl_formatado)

        raiz = ET.Element("xmeml", version="4")
        sequencia = ET.SubElement(raiz, "sequence")
        ET.SubElement(sequencia, "name").text = "Aula_Cortada"
        ET.SubElement(sequencia, "duration").text = "100000"
        _adicionar_rate(sequencia)

        media = ET.SubElement(sequencia, "media")
        video = ET.SubElement(media, "video")
        _adicionar_format_video(video)
        trilha_v1 = ET.SubElement(video, "track")
        trilha_v2 = ET.SubElement(video, "track")

        total_v1 = 0
        total_v2 = 0
        for indice, segmento in enumerate(segmentos):
            frame_start = seconds_to_frames(float(segmento["start"]), fps)
            frame_end = seconds_to_frames(float(segmento["end"]), fps)

            if str(segmento["track"]) == "V1":
                trilha = trilha_v1
                total_v1 += 1
            else:
                trilha = trilha_v2
                total_v2 += 1

            clipitem = ET.SubElement(trilha, "clipitem", id=f"clip_{indice}")
            ET.SubElement(clipitem, "name").text = str(segmento.get("text", nome_arquivo))
            ET.SubElement(clipitem, "enabled").text = "TRUE"
            ET.SubElement(clipitem, "start").text = str(frame_start)
            ET.SubElement(clipitem, "end").text = str(frame_end)
            ET.SubElement(clipitem, "in").text = str(frame_start)
            ET.SubElement(clipitem, "out").text = str(frame_end)
            _adicionar_rate(clipitem)

        primeiro_ficheiro_declarado = False
        for clipitem in list(trilha_v1) + list(trilha_v2):
            if not primeiro_ficheiro_declarado:
                arquivo = ET.SubElement(clipitem, "file", id="file_1")
                ET.SubElement(arquivo, "name").text = nome_arquivo
                ET.SubElement(arquivo, "pathurl").text = pathurl_formatado
                _adicionar_rate(arquivo)
                primeiro_ficheiro_declarado = True
            else:
                ET.SubElement(clipitem, "file", id="file_1")

        logger.info("Clipes no XML: V1=%s, V2=%s.", total_v1, total_v2)

        xml_documento = _serializar_xmeml(raiz)

        diretorio = os.path.dirname(os.path.abspath(output_path))
        if diretorio:
            os.makedirs(diretorio, exist_ok=True)

        with open(output_path, "w", encoding="utf-8", newline="\n") as arquivo_xml:
            arquivo_xml.write(xml_documento)

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


def _serializar_xmeml(raiz: ET.Element) -> str:
    """Garante o cabeçalho obrigatório do Premiere e serializa o restante da árvore."""
    sequencia = raiz.find("sequence")
    if sequencia is None:
        raise ValueError("Árvore XML sem <sequence>.")

    ET.indent(sequencia, space="  ")
    sequencia_xml = ET.tostring(sequencia, encoding="unicode")
    sequencia_indentada = "\n".join(
        f"  {linha}" if linha else linha for linha in sequencia_xml.splitlines()
    )
    return f"{CABECALHO_XMEML}\n{sequencia_indentada}\n</xmeml>\n"


def _adicionar_rate(parent: ET.Element) -> None:
    """Insere o bloco de frame rate obrigatório do Premiere (30 fps, non-NTSC)."""
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = "30"
    ET.SubElement(rate, "ntsc").text = "FALSE"


def _adicionar_format_video(video: ET.Element) -> None:
    """Insere o <format> 1920x1080 obrigatório antes das tracks de vídeo."""
    formato = ET.SubElement(video, "format")
    caracteristicas = ET.SubElement(formato, "samplecharacteristics")
    _adicionar_rate(caracteristicas)
    ET.SubElement(caracteristicas, "width").text = "1920"
    ET.SubElement(caracteristicas, "height").text = "1080"
    ET.SubElement(caracteristicas, "pixelaspectratio").text = "square"


def _caminho_como_mp4(file_path: str) -> str:
    """Troca extensão de áudio (.wav/.mp3) por .mp4 nas referências do XML."""
    raiz, extensao = os.path.splitext(file_path)
    if extensao.lower() in {".wav", ".mp3"}:
        return f"{raiz}.mp4"
    return file_path
