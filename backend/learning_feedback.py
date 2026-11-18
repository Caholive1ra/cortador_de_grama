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


def comparar_xmls(
    xml_sugerido: str | list[str],
    xml_revisado: str,
    xml_origem: str | None = None,
    project_type: str | None = None,
) -> dict:
    """Compara a area visivel de duas timelines FCP XML, em frames.

    A uniao dos ``clipitem`` habilitados nas trilhas de video representa o
    material mantido. A comparacao usa ``in/out`` (tempo na midia-fonte), e
    nao ``start/end`` (tempo na sequencia): o Premiere pode fechar os cortes
    com ripple e reiniciar a timeline em zero apos uma revisao.
    """
    perfil = _perfil_de_aprendizado(project_type)
    logger.info("Comparando XML sugerido e revisado (%s).", perfil["id"])
    xmls_sugeridos = _normalizar_xmls(xml_sugerido)
    origem = _ler_origem_composta(_normalizar_xmls(xml_origem)) if xml_origem else _ler_origem_composta(xmls_sugeridos)
    # Quando o proprio original e composto, ele ja foi lido e deslocado por
    # _ler_origem_composta. Um candidato de avaliacao continua sendo um XML.
    sugerido = _ler_timeline(xml_sugerido, source_offsets=origem["source_offsets"]) if isinstance(xml_sugerido, str) else origem
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
        "schema_version": 4,
        "learning_profile": perfil,
        "source_xmls": xmls_sugeridos,
        "unit": "frames",
        "timebase": sugerido["timebase"],
        "fps": sugerido["fps"],
        "warnings": warnings,
        "ignored_graphics": sorted(set(origem["ignored_graphics"] + sugerido["ignored_graphics"] + revisado["ignored_graphics"])),
        "validated_sources": not warnings,
        # O mesmo conjunto de midias pode existir em uma aula e em um
        # videocast. Eles nao podem compartilhar referencia nem benchmark.
        "source_group": hashlib.sha256(json.dumps({
            "sources": sorted(sugerido["sources"]), "profile": perfil["id"],
        }, sort_keys=True).encode()).hexdigest(),
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
    comparacao: dict, xml_sugerido: str | list[str], xml_revisado: str,
    project_type: str | None = None, diagnostic_path: str | None = None,
) -> tuple[str, dict]:
    """Persiste exemplos divergentes, sem copiar audio ou video.

    O diagnostico da execucao original e encontrado pelo mesmo ``job_id`` do
    XML. Guardamos somente segmentos que divergem da decisao humana, para que
    a base seja pequena e diretamente util a avaliacao futura.
    """
    xmls_sugeridos = _normalizar_xmls(xml_sugerido)
    raiz = DATA_DIR
    raiz.mkdir(parents=True, exist_ok=True)
    diagnostico, caminhos_diagnostico = _carregar_diagnosticos_compostos(
        xmls_sugeridos, diagnostic_path,
    )
    exemplos = _extrair_exemplos(comparacao, diagnostico)
    identidade = hashlib.sha256(json.dumps({
        "comparison": comparacao, "examples": exemplos, "project_type": project_type,
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
    destino = raiz / f"feedback_{identidade}.json"
    duplicado = destino.exists()
    for numero, exemplo in enumerate(exemplos):
        exemplo.update(id=str(numero), status="pending", reason=None, note="")
    perfil = comparacao.get("learning_profile") or _perfil_de_aprendizado(project_type)
    registro = {
        "schema_version": 4,
        "id": identidade,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_type": perfil["project_type"],
        "learning_profile": perfil,
        "suggested_xml": os.path.abspath(xmls_sugeridos[0]),
        "suggested_xmls": [os.path.abspath(item) for item in xmls_sugeridos],
        "revised_xml": os.path.abspath(xml_revisado),
        "diagnostic_path": caminhos_diagnostico[0] if caminhos_diagnostico else None,
        "diagnostic_paths": caminhos_diagnostico,
        "comparison": comparacao,
        "examples": exemplos,
        "source_group": comparacao.get("source_group"),
        "dataset_role": "reference",
        "eligible": bool(comparacao.get("validated_sources") and diagnostico and perfil["project_type"] in ("videoaula", "videocast", "videocast_graduacao", "videocast_mba")),
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


def _normalizar_xmls(xmls: str | list[str]) -> list[str]:
    """Aceita um ou varios XMLs originais que foram unidos no Premiere."""
    resultado = [xmls] if isinstance(xmls, str) else list(xmls or [])
    resultado = [str(item).strip() for item in resultado if str(item).strip()]
    if not resultado:
        raise ValueError("Selecione ao menos um XML original gerado pela IA.")
    if len(set(map(os.path.normcase, resultado))) != len(resultado):
        raise ValueError("O mesmo XML original foi selecionado mais de uma vez.")
    # Processamentos fragmentados usam PGM_01, PGM_1.1, PGM_1.2 etc. O
    # seletor do Windows nem sempre preserva a ordem do clique, entao usamos
    # essa numeracao quando todos os XMLs pertencem ao mesmo PGM.
    chaves = [_chave_de_ordem_pgm(caminho) for caminho in resultado]
    if all(chave is not None for chave in chaves) and len({chave[0] for chave in chaves}) == 1:
        resultado = [caminho for _, caminho in sorted(zip(chaves, resultado))]
    return resultado


def _chave_de_ordem_pgm(caminho: str) -> tuple[str, int, int] | None:
    nome = Path(caminho).stem.lower()
    encontrado = re.search(r"^(.*_pgm_)(\d+)(?:\.(\d+))?_cortado(?:_[a-f0-9]+)?$", nome)
    if not encontrado:
        return None
    return encontrado.group(1), int(encontrado.group(2)), int(encontrado.group(3) or -1)


def _carregar_diagnosticos_compostos(
    xmls: list[str], explicit_path: str | None,
) -> tuple[dict | None, list[str]]:
    """Une o contexto dos processamentos na mesma ordem da aula final."""
    segmentos, caminhos = [], []
    deslocamento = 0.0
    for indice, xml in enumerate(xmls):
        # Um caminho informado pelo painel so e confiavel para um original.
        diagnostico, caminho = _carregar_diagnostico(xml, explicit_path if len(xmls) == 1 and indice == 0 else None)
        timeline = _ler_timeline(xml)
        if diagnostico and isinstance(diagnostico.get("segmentos"), list):
            for segmento in diagnostico["segmentos"]:
                copia = dict(segmento)
                copia["start"] = round(float(copia.get("start", 0)) + deslocamento, 3)
                copia["end"] = round(float(copia.get("end", 0)) + deslocamento, 3)
                segmentos.append(copia)
            if caminho:
                caminhos.append(caminho)
        deslocamento += timeline["duration"] / timeline["fps"]
    return ({"segmentos": segmentos} if segmentos else None), caminhos


def _ler_origem_composta(xmls: list[str]) -> dict:
    """Compoe originais independentes na ordem em que foram unidos.

    Cada processamento comeca seu relogio em zero. Ao unir, por exemplo,
    ``PGM_01`` e ``PGM_1.1`` no Premiere, deslocamos o relogio das fontes do
    segundo XML pela duracao do primeiro antes de comparar com a timeline.
    """
    partes = [_ler_timeline(caminho) for caminho in xmls]
    fps = partes[0]["fps"]
    if any(parte["fps"] != fps for parte in partes[1:]):
        raise ValueError("Os XMLs originais compostos usam frame rates diferentes.")
    cursor_segundos = 0.0
    offsets, kept, sources, graficos = {}, [], set(), []
    for indice, parte in enumerate(partes):
        sobrepostas = sources & set(parte["sources"])
        if sobrepostas:
            raise ValueError("Os XMLs compostos reutilizam a mesma midia-fonte; importe cada processamento apenas uma vez.")
        offsets_da_parte = {
            nome: valor + cursor_segundos for nome, valor in parte["source_offsets"].items()
        }
        relida = _ler_timeline(xmls[indice], source_offsets=offsets_da_parte)
        # A relida acima preserva in/out e aplica o relogio composto.
        kept.extend(relida["kept"])
        offsets.update(offsets_da_parte)
        sources.update(parte["sources"])
        graficos.extend(parte["ignored_graphics"])
        cursor_segundos += parte["duration"] / fps
    return {
        "timebase": partes[0]["timebase"], "duration": round(cursor_segundos * fps),
        "kept": _unir(kept), "source_offsets": offsets, "fps": fps,
        "sources": sorted(sources), "identified": all(parte["identified"] for parte in partes),
        "ignored_graphics": graficos,
    }


def _perfil_de_aprendizado(project_type: str | None) -> dict:
    """Define o que um registro pode ensinar, sem misturar contextos."""
    # Compatibilidade com chamadas internas antigas. O painel atual sempre
    # envia o tipo explicitamente; registros novos continuam versionados.
    if project_type is None:
        project_type = "videoaula"
    if project_type not in ("videoaula", "videocast", "videocast_graduacao", "videocast_mba"):
        raise ValueError("Escolha videoaula, videocast de graduacao ou videocast MBA.")
    if project_type in ("videocast", "videocast_graduacao", "videocast_mba"):
        perfil = "videocast_mba_editorial_v1" if project_type == "videocast_mba" else ("videocast_graduacao_editorial_v1" if project_type == "videocast_graduacao" else "videocast_editorial_camera_v1")
        return {
            "id": perfil,
            "project_type": project_type,
            "label": "Videocast — cortes e direcao de cameras",
            "focus": ["cortes de fala", "ritmo de conversa", "direcao de cameras"],
            "camera_direction": True,
        }
    return {
        "id": "videoaula_editorial_v1",
        "project_type": "videoaula",
        "label": "Videoaula — montagem didatica",
        "focus": ["cortes de fala", "clareza didatica", "ritmo", "tela e recursos de aula"],
        "camera_direction": False,
    }


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
    # O Premiere pode colocar uma sequencia aninhada apenas para organizar o
    # audio. Ela nao muda os cortes visuais que comparamos aqui e, portanto,
    # deve ser ignorada. Ainda rejeitamos sequencias aninhadas com video para
    # nao interpretar uma edicao visual composta como se fosse um corte simples.
    sequencia = raiz.find("./sequence")
    if sequencia is None:
        sequencia = raiz.find(".//sequence")
    if sequencia is None:
        raise ValueError("XML sem sequencia FCP valida.")
    aninhadas_com_video = [
        aninhada for aninhada in sequencia.findall(".//sequence")
        if aninhada.findall("./media/video/track/clipitem")
    ]
    if aninhadas_com_video:
        raise ValueError(
            "O XML possui uma sequencia aninhada com video. Sequencias aninhadas "
            "somente de audio sao aceitas; achate o video antes de registrar o aprendizado."
        )
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
        if _e_grafico(nome):
            graficos.append(nome)
            continue
        if any((efeito.findtext("effectid", "").lower() in ("timeremap", "speed")) for efeito in clip.findall("./filter/effect")):
            raise ValueError("XML com alteracao de velocidade nao pode gerar feedback confiavel.")
        source_rate = float(fonte.get("fps") or fps)
        inicio_bruto = int(clip.findtext("start") or 0)
        fim_bruto = int(clip.findtext("end") or 0)
        # O exportador FCP XML do Premiere usa -1 no inicio/fim de um
        # clipitem que toca uma transicao. Nao e uma midia invalida: os
        # campos in/out continuam contendo exatamente o trecho de origem.
        # Como o aprendizado compara in/out, ignoramos apenas esse relogio
        # de timeline ambíguo ao descobrir o offset da fonte.
        inicio_timeline = inicio_bruto / fps if inicio_bruto >= 0 and fim_bruto >= 0 else None
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


def _e_grafico(nome: str) -> bool:
    """Identifica arte de apoio que nao deve virar conteudo de aula."""
    nome_normalizado = nome.replace("\\", "/").lower()
    extensoes = (".png", ".jpg", ".jpeg", ".psd", ".tif", ".tiff", ".bmp", ".gif", ".aegraphic")
    return (
        nome_normalizado.endswith(extensoes)
        or "/motion graphics template media/" in nome_normalizado
        or "/uteis edicao/" in nome_normalizado
        or "vht_" in os.path.basename(nome_normalizado)
        or "vinheta" in os.path.basename(nome_normalizado)
    )


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
        if item["timeline_start"] is None:
            continue
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
