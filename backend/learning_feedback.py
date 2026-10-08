"""Extrai feedback editorial comparando o XML sugerido e o XML revisado.

O modulo nao altera regras nem modelos. Ele apenas transforma uma revisao real
no Premiere em um registro auditavel, que podera alimentar avaliacao e treino.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent / "learning_data"


def comparar_xmls(xml_sugerido: str, xml_revisado: str, xml_origem: str | None = None) -> dict:
    """Compara a area visivel de duas timelines FCP XML, em frames.

    A uniao dos ``clipitem`` habilitados nas trilhas de video representa o
    material mantido. A comparacao usa ``in/out`` (tempo na midia-fonte), e
    nao ``start/end`` (tempo na sequencia): o Premiere pode fechar os cortes
    com ripple e reiniciar a timeline em zero apos uma revisao.
    """
    logger.info("Comparando XML sugerido e revisado.")
    origem = _ler_timeline(xml_origem) if xml_origem else _ler_timeline(xml_sugerido)
    sugerido = _ler_timeline(xml_sugerido, source_offsets=origem["source_offsets"])
    revisado = _ler_timeline(xml_revisado, source_offsets=origem["source_offsets"])
    if sugerido["fps"] != revisado["fps"]:
        raise ValueError("Os XMLs usam frame rates diferentes e nao podem ser comparados.")
    desconhecidas = (set(revisado["sources"]) | set(sugerido["sources"])) - set(origem["sources"])
    if desconhecidas:
        raise ValueError("O XML revisado contem fontes que nao pertencem ao original. Exporte somente a sequencia revisada do mesmo material.")
    warnings = []
    if not sugerido["identified"] or not revisado["identified"]:
        warnings.append("Fontes sem caminho de midia: comparacao disponivel apenas para consulta, sem exemplos aprovaveis.")

    limite = max(sugerido["duration"], revisado["duration"])
    previstos, finais = sugerido["kept"], revisado["kept"]
    aceitos = _intersecao(previstos, finais)
    ia_manteve_editor_cortou = _subtrair(previstos, finais)
    ia_cortou_editor_manteve = _subtrair(finais, previstos)
    total = sum(fim - inicio for inicio, fim in aceitos + ia_manteve_editor_cortou + ia_cortou_editor_manteve)

    return {
        "schema_version": 3,
        "unit": "frames",
        "timebase": sugerido["timebase"],
        "fps": sugerido["fps"],
        "warnings": warnings,
        "ignored_graphics": sorted(set(origem["ignored_graphics"] + sugerido["ignored_graphics"] + revisado["ignored_graphics"])),
        "validated_sources": not warnings,
        "source_group": hashlib.sha256(json.dumps(sorted(sugerido["sources"])).encode()).hexdigest(),
        "duration_frames": limite,
        "suggested_kept": previstos,
        "final_kept": finais,
        "accepted_kept": aceitos,
        "ai_kept_editor_cut": ia_manteve_editor_cortou,
        "ai_cut_editor_kept": ia_cortou_editor_manteve,
        "metrics": {
            "agreement_frames": sum(fim - inicio for inicio, fim in aceitos),
            "ai_kept_editor_cut_frames": sum(fim - inicio for inicio, fim in ia_manteve_editor_cortou),
            "ai_cut_editor_kept_frames": sum(fim - inicio for inicio, fim in ia_cortou_editor_manteve),
            "agreement_rate": round(sum(fim - inicio for inicio, fim in aceitos) / total, 4) if total else 1.0,
            "useful_removed_seconds": round(sum(f - i for i, f in ia_cortou_editor_manteve) / sugerido["fps"], 3),
            "unwanted_kept_seconds": round(sum(f - i for i, f in ia_manteve_editor_cortou) / sugerido["fps"], 3),
        },
    }


def salvar_feedback(
    comparacao: dict, xml_sugerido: str, xml_revisado: str,
    project_type: str | None = None, diagnostic_path: str | None = None,
) -> tuple[str, dict]:
    """Persiste exemplos divergentes, sem copiar audio ou video.

    O diagnostico da execucao original e encontrado pelo mesmo ``job_id`` do
    XML. Guardamos somente segmentos que divergem da decisao humana, para que
    a base seja pequena e diretamente util a avaliacao futura.
    """
    raiz = DATA_DIR
    raiz.mkdir(parents=True, exist_ok=True)
    diagnostico, caminho_diagnostico = _carregar_diagnostico(xml_sugerido, diagnostic_path)
    exemplos = _extrair_exemplos(comparacao, diagnostico)
    identidade = hashlib.sha256(json.dumps({
        "comparison": comparacao, "examples": exemplos, "project_type": project_type,
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
    destino = raiz / f"feedback_{identidade}.json"
    duplicado = destino.exists()
    for numero, exemplo in enumerate(exemplos):
        exemplo.update(id=str(numero), status="pending", reason=None, note="")
    registro = {
        "schema_version": 3,
        "id": identidade,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_type": project_type or "unknown",
        "suggested_xml": os.path.abspath(xml_sugerido),
        "revised_xml": os.path.abspath(xml_revisado),
        "diagnostic_path": caminho_diagnostico,
        "comparison": comparacao,
        "examples": exemplos,
        "source_group": comparacao.get("source_group"),
        "dataset_role": "reference",
        "eligible": bool(comparacao.get("validated_sources") and diagnostico and project_type in ("videoaula", "videocast")),
    }
    if not duplicado:
        destino.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    resumo = {
        "examples_saved": len(exemplos),
        "diagnostic_found": diagnostico is not None,
        "feedback_id": identidade,
        "duplicate": duplicado,
        "warnings": comparacao.get("warnings", []),
        "by_editor_action": {
            "editor_cut": sum(item["editor_action"] == "cut" for item in exemplos),
            "editor_kept": sum(item["editor_action"] == "keep" for item in exemplos),
        },
    }
    return str(destino), resumo


def _carregar_diagnostico(xml_sugerido: str, explicit_path: str | None) -> tuple[dict | None, str | None]:
    esperado = str(xml_sugerido).replace("_cortado_", "_diagnostico_")
    esperado = os.path.splitext(esperado)[0] + ".json"
    if explicit_path and os.path.normcase(os.path.abspath(explicit_path)) != os.path.normcase(os.path.abspath(esperado)):
        raise ValueError("O diagnostico selecionado nao corresponde ao XML original. Selecione novamente o par de XMLs correto.")
    candidato = explicit_path
    if not candidato:
        candidato = str(xml_sugerido).replace("_cortado_", "_diagnostico_")
        if candidato.lower().endswith(".xml"):
            candidato = candidato[:-4] + ".json"
    arquivo = Path(candidato)
    if not arquivo.is_file():
        return None, None
    try:
        bruto = json.loads(arquivo.read_text(encoding="utf-8"))
        return bruto if isinstance(bruto, dict) else None, str(arquivo.resolve())
    except (OSError, json.JSONDecodeError):
        return None, None


def _extrair_exemplos(comparacao: dict, diagnostico: dict | None) -> list[dict]:
    if not diagnostico:
        return []
    segmentos = diagnostico.get("segmentos")
    if not isinstance(segmentos, list):
        return []
    fps = float(comparacao.get("fps", comparacao["timebase"]))
    saida = []
    for intervalos, acao_ia, acao_editor in (
        (comparacao["ai_kept_editor_cut"], "keep", "cut"),
        (comparacao["ai_cut_editor_kept"], "cut", "keep"),
    ):
        for inicio_frame, fim_frame in intervalos:
            inicio, fim = inicio_frame / fps, fim_frame / fps
            relacionados = [
                segmento for segmento in segmentos
                if float(segmento.get("end", 0)) > inicio and float(segmento.get("start", 0)) < fim
            ]
            if not relacionados:
                continue
            saida.append({
                "ai_action": acao_ia,
                "editor_action": acao_editor,
                "start_seconds": round(inicio, 3),
                "end_seconds": round(fim, 3),
                "context_before": [dict(start=s["start"], end=s["end"], text=s.get("text", "")) for s in segmentos
                                   if float(s.get("end", 0)) <= inicio and float(s.get("end", 0)) >= inicio - 45],
                "context_after": [dict(start=s["start"], end=s["end"], text=s.get("text", "")) for s in segmentos
                                  if float(s.get("start", 0)) >= fim and float(s.get("start", 0)) <= fim + 75],
                "segments": [{
                    "start": round(float(item.get("start", 0)), 3),
                    "end": round(float(item.get("end", 0)), 3),
                    "text": str(item.get("text", "")),
                    "ai_reason": item.get("reason"),
                    "ai_review": bool(item.get("review", False)),
                } for item in relacionados],
            })
    return saida


def _ler_timeline(path: str, source_offsets: dict[str, float] | None = None) -> dict:
    arquivo = Path(path)
    if not arquivo.is_file():
        raise FileNotFoundError(f"XML nao encontrado: {path}")
    try:
        raiz = ET.parse(arquivo).getroot()
    except ET.ParseError as exc:
        raise ValueError("XML invalido ou incompleto.") from exc
    if len(raiz.findall(".//sequence")) != 1:
        raise ValueError("Exporte uma unica sequencia FCP XML, sem sequencias aninhadas.")
    sequencia = raiz.find(".//sequence")
    if sequencia is None:
        raise ValueError("XML sem sequencia FCP valida.")
    timebase = int(sequencia.findtext("rate/timebase") or 30)
    if timebase <= 0:
        raise ValueError("Frame rate invalido.")
    fps = timebase / 1.001 if sequencia.findtext("rate/ntsc", "FALSE").upper() == "TRUE" else float(timebase)
    duration = int(sequencia.findtext("duration") or 0)
    files = _arquivos_por_id(sequencia)
    clips = sequencia.findall("./media/video/track/clipitem")
    if not clips:
        raise ValueError("XML sem clipes de video comparaveis.")
    if any(t.findtext("enabled", "TRUE").upper() == "FALSE" for t in sequencia.findall("./media/video/track")):
        raise ValueError("Comparacao de trilhas inteiras desativadas ainda nao suportada.")
    itens = []
    graficos = []
    for clip in clips:
        arquivo = clip.find("file")
        file_id = arquivo.get("id") if arquivo is not None else None
        fonte = files.get(file_id, {})
        nome = fonte.get("identity") or fonte.get("name") or "__default__"
        if os.path.splitext(nome)[1].lower() in (".png", ".jpg", ".jpeg", ".psd", ".tif", ".tiff", ".bmp", ".gif"):
            graficos.append(nome)
            continue
        if any((efeito.findtext("effectid", "").lower() in ("timeremap", "speed")) for efeito in clip.findall("./filter/effect")):
            raise ValueError("XML com alteracao de velocidade nao pode gerar feedback confiavel.")
        source_rate = float(fonte.get("fps") or fps)
        inicio_timeline = int(clip.findtext("start") or 0) / fps
        if int(clip.findtext("start") or 0) < 0 or int(clip.findtext("end") or 0) < 0:
            raise ValueError("XML com transicoes sobrepostas nao suportado para aprendizado.")
        inicio_fonte = int(clip.findtext("in") or clip.findtext("start") or 0) / source_rate
        fim_fonte = int(clip.findtext("out") or clip.findtext("end") or 0) / source_rate
        if fim_fonte > inicio_fonte:
            itens.append({
                "name": nome, "enabled": (clip.findtext("enabled") or "TRUE").upper() == "TRUE",
                "timeline_start": inicio_timeline, "source_start": inicio_fonte, "source_end": fim_fonte,
            })
    offsets = source_offsets if source_offsets is not None else _descobrir_offsets(itens)
    if not itens:
        raise ValueError("XML sem trechos de video: imagens graficas nao representam cortes de fala.")
    kept = []
    for item in itens:
        if not item["enabled"] or item["name"] not in offsets:
            continue
        inicio = round((item["source_start"] + offsets[item["name"]]) * fps)
        fim = round((item["source_end"] + offsets[item["name"]]) * fps)
        if fim > inicio:
            kept.append((inicio, fim))
    return {
        "timebase": timebase, "duration": duration, "kept": _unir(kept),
        "source_offsets": offsets,
        "fps": fps,
        "sources": sorted({i["name"] for i in itens}),
        "identified": bool(files) and all(i["name"].startswith("file:") for i in itens),
        "ignored_graphics": graficos,
    }


def _arquivos_por_id(sequencia: ET.Element) -> dict[str, dict]:
    """Resolve o id compacto do clip para nome e fps da midia-fonte."""
    resultado = {}
    for arquivo in sequencia.findall(".//file"):
        file_id = arquivo.get("id")
        nome = arquivo.findtext("name")
        if file_id and nome:
            rate = int(arquivo.findtext("rate/timebase") or 0)
            ntsc = arquivo.findtext("rate/ntsc", "FALSE").upper() == "TRUE"
            caminho = unquote(arquivo.findtext("pathurl") or "").replace("\\", "/").lower()
            caminho = re.sub(r"^file://(?:localhost)?/", "file:///", caminho)
            resultado[file_id] = {
                "name": nome,
                "timebase": arquivo.findtext("rate/timebase"),
                "fps": rate / 1.001 if ntsc else rate,
                "identity": caminho or nome,
            }
    return resultado


def _descobrir_offsets(itens: list[dict]) -> dict[str, float]:
    """Mapeia o relogio de cada camera para o relogio do PGM.

    No XML original, ``start`` e o tempo da sequencia/PGM e ``in`` e o tempo
    do arquivo da camera. A mediana protege contra pequenos arredondamentos.
    """
    por_fonte: dict[str, list[float]] = {}
    for item in itens:
        por_fonte.setdefault(item["name"], []).append(
            item["timeline_start"] - item["source_start"]
        )
    return {
        nome: sorted(valores)[len(valores) // 2]
        for nome, valores in por_fonte.items()
    }


def _unir(intervalos: list[tuple[int, int]]) -> list[list[int]]:
    unidos: list[list[int]] = []
    for inicio, fim in sorted(intervalos):
        if unidos and inicio <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], fim)
        else:
            unidos.append([inicio, fim])
    return unidos


def _intersecao(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    resultado = []
    for ai, af in a:
        for bi, bf in b:
            inicio, fim = max(ai, bi), min(af, bf)
            if fim > inicio:
                resultado.append((inicio, fim))
    return _unir(resultado)


def _subtrair(base: list[list[int]], remover: list[list[int]]) -> list[list[int]]:
    atual = [tuple(item) for item in base]
    for ri, rf in remover:
        proximo = []
        for inicio, fim in atual:
            if rf <= inicio or ri >= fim:
                proximo.append((inicio, fim))
            else:
                if inicio < ri:
                    proximo.append((inicio, ri))
                if rf < fim:
                    proximo.append((rf, fim))
        atual = proximo
    return _unir(atual)
