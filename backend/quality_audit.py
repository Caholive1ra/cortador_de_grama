"""Auditoria nao destrutiva da sequencia final de uma aula.

Os achados sao avisos para o editor. Este modulo nunca altera XML, midia ou
efeitos no Premiere. Analises que dependem de interpretacao humana recebem o
rotulo ``CHECAR`` em vez de serem tratadas como erro automatico.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import wave
import xml.etree.ElementTree as ET
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


CORES = {
    "jump_cut": "red",
    "continuidade": "orange",
    "repeticao": "yellow",
    "audio": "cyan",
    "imagem": "blue",
    "conteudo": "magenta",
    "revisao": "green",
}

_PALAVRAS_COMUNS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma",
    "para", "por", "com", "que", "na", "no", "eu", "voce", "vai", "vamos", "entao",
}
_SINAIS_REFAZ = re.compile(
    r"\b(vou (?:refazer|repetir|explicar de novo)|deixa eu (?:refazer|explicar)|"
    r"melhor (?:dizendo|explicando)|ou melhor|na verdade|quer dizer|perdao)\b", re.I,
)


def auditar_sequencia(xml_path: str, media_path: str, segmentos: list[dict]) -> dict:
    """Audita a montagem final e devolve achados ordenados por tempo.

    ``media_path`` deve ser um render/proxy da sequencia final: ele permite
    validar audio e imagem reais, que o XML sozinho nao contem.
    """
    xml = Path(xml_path)
    media = Path(media_path)
    if not xml.is_file():
        raise FileNotFoundError(f"XML nao encontrado: {xml_path}")
    if not media.is_file():
        raise FileNotFoundError(f"Video final nao encontrado: {media_path}")
    timeline = _ler_xml(xml)
    logger.info("Auditoria tecnica: XML lido | cortes=%s | clipes=%s | segmentos=%s",
                len(timeline["cuts"]), len(timeline["clips"]), len(segmentos))
    achados = _auditar_cortes(timeline)
    logger.info("Auditoria tecnica: cortes/jump-cuts | achados=%s", len(achados))
    achados.extend(_auditar_texto(segmentos))
    logger.info("Auditoria tecnica: texto local | achados=%s", len(achados))
    revisao_ia = _revisar_repeticoes_com_ia(segmentos)
    achados.extend(revisao_ia["findings"])
    logger.info("Auditoria tecnica: revisao IA de repeticoes | status=%s | achados=%s",
                revisao_ia["status"], len(revisao_ia["findings"]))
    achados.extend(_auditar_audio(str(media), timeline["cuts"]))
    logger.info("Auditoria tecnica: audio concluido | achados=%s", len(achados))
    achados.extend(_auditar_imagem(str(media), timeline["cuts"], timeline["fps"]))
    logger.info("Auditoria tecnica: imagem concluida | achados=%s", len(achados))
    achados.sort(key=lambda item: (item["start"], item["category"], item["title"]))
    return {
        "findings": achados,
        "summary": dict(Counter(item["category"] for item in achados)),
        "checked": {
            "cuts": len(timeline["cuts"]), "transcript_segments": len(segmentos),
            "media": str(media.resolve()), "xml": str(xml.resolve()),
            "text_ai_review": revisao_ia["status"],
        },
    }


def _ler_xml(path: Path) -> dict:
    try:
        sequence = ET.parse(path).getroot().find("./sequence")
    except ET.ParseError as exc:
        raise ValueError("XML invalido ou incompleto.") from exc
    if sequence is None:
        raise ValueError("XML sem sequencia FCP valida.")
    timebase = int(sequence.findtext("rate/timebase") or 30)
    ntsc = sequence.findtext("rate/ntsc", "FALSE").upper() == "TRUE"
    fps = timebase / 1.001 if ntsc else float(timebase)
    arquivos: dict[str, str] = {}
    for arquivo in sequence.findall(".//file"):
        arquivo_id = arquivo.get("id")
        identificacao = arquivo.findtext("pathurl") or arquivo.findtext("name")
        if arquivo_id and identificacao:
            arquivos[arquivo_id] = identificacao
    clips = []
    # Cada trilha pode ter sobreposicoes; auditoria de jump cut vale apenas
    # para clipes adjacentes na mesma trilha e provenientes da mesma fonte.
    for track_index, track in enumerate(sequence.findall("./media/video/track")):
        for clip in track.findall("clipitem"):
            if clip.findtext("enabled", "TRUE").upper() == "FALSE":
                continue
            start, end = int(clip.findtext("start") or 0), int(clip.findtext("end") or 0)
            if end <= start:
                continue
            file_node = clip.find("file")
            source = ""
            if file_node is not None:
                source = (file_node.findtext("pathurl") or file_node.findtext("name")
                          or arquivos.get(file_node.get("id") or "", ""))
            scale = _escala(clip)
            clips.append({"track": track_index, "start": start / fps, "end": end / fps,
                          "source": source, "scale": scale})
    cuts = sorted({round(item["end"], 3) for item in clips})
    return {"fps": fps, "clips": clips, "cuts": cuts}


def _escala(clip: ET.Element) -> float:
    for parameter in clip.findall(".//parameter"):
        if (parameter.findtext("parameterid") or "").lower() == "scale":
            try:
                return float(parameter.findtext("value") or 100)
            except ValueError:
                return 100.0
    return 100.0


def _achado(category: str, start: float, title: str, description: str, end: float | None = None) -> dict:
    return {"category": category, "color": CORES[category], "start": round(start, 3),
            "end": round(end if end is not None else start + 0.25, 3),
            "title": title, "description": description}


def _auditar_cortes(timeline: dict) -> list[dict]:
    achados = []
    by_track: dict[int, list[dict]] = {}
    for clip in timeline["clips"]:
        by_track.setdefault(clip["track"], []).append(clip)
    for clips in by_track.values():
        clips.sort(key=lambda item: item["start"])
        for antes, depois in zip(clips, clips[1:]):
            if abs(antes["end"] - depois["start"]) > 1 / timeline["fps"] + 0.01:
                continue
            if antes["source"] and antes["source"] == depois["source"] and abs(antes["scale"] - depois["scale"]) < 1:
                achados.append(_achado(
                    "jump_cut", depois["start"], "JUMP CUT: conferir enquadramento",
                    "Corte no mesmo arquivo sem mudanca de escala registrada. Confirme se o segundo plano tem reframe/zoom perceptivel.",
                ))
    return achados


def _auditar_texto(segmentos: list[dict]) -> list[dict]:
    achados = []
    limpos = [dict(item, text=str(item.get("text", "")).strip()) for item in segmentos]
    for anterior, atual in zip(limpos, limpos[1:]):
        a, b = anterior["text"], atual["text"]
        if not a or not b:
            continue
        # Inicio em conectivo apos uma fala sem fechamento e um forte sinal de
        # frase mutilada; e apenas aviso, pois transcricoes nao pontuam sempre.
        if re.match(r"^(e |mas |portanto|por isso|assim|que |porque )", b.lower()) and not re.search(r"[.!?…:]$", a):
            achados.append(_achado("continuidade", float(atual["start"]), "CONTINUIDADE: frase pode estar quebrada",
                "O trecho inicia como continuacao da fala anterior. Ouça a emenda e confirme se o corte preserva o sentido."))
        palavras_a = _palavras(a)
        palavras_b = _palavras(b)
        if len(palavras_a) >= 5 and len(palavras_b) >= 5 and _similaridade(palavras_a, palavras_b) >= .78:
            achados.append(_achado("repeticao", float(atual["start"]), "REPETIÇÃO: fala semelhante",
                "Trechos consecutivos possuem alta sobreposicao de palavras. Confirme se e repeticao didatica intencional."))
    achados.extend(_detectar_repeticoes_locais(limpos))
    for item in limpos:
        text = item["text"]
        # Numeros, percentuais e datas merecem verificacao, mas nao constituem
        # prova de erro. A validacao factual depende de fontes do curso.
        if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:%|anos?|meses?|mil|milhoes?)(?=$|\s|[.,;:])", text.lower()):
            achados.append(_achado("conteudo", float(item["start"]), "CHECAR CONTEÚDO: afirmação factual",
                "Há numero, data ou medida na fala. Confirme a informacao com o material oficial do curso antes da entrega.", float(item["end"])))
    return achados


def _palavras(texto: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿ0-9]+", texto.lower()))


def _similaridade(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


def _texto_janela(segmentos: list[dict], inicio: int, fim: int) -> str:
    return " ".join(str(item["text"]) for item in segmentos[inicio:fim]).strip()


def _palavras_significativas(texto: str) -> set[str]:
    return {palavra for palavra in re.findall(r"[a-zA-Z0-9]+", texto.lower())
            if palavra not in _PALAVRAS_COMUNS}


def _detectar_repeticoes_locais(segmentos: list[dict]) -> list[dict]:
    """Compara janelas de 1 a 3 falas; cobre repeticao dividida pelo Whisper."""
    achados, vistos = [], set()
    for atual_inicio in range(1, len(segmentos)):
        for tamanho_atual in range(1, min(3, len(segmentos) - atual_inicio) + 1):
            atual_fim = atual_inicio + tamanho_atual
            atual = _texto_janela(segmentos, atual_inicio, atual_fim)
            palavras_atual = _palavras_significativas(atual)
            if len(palavras_atual) < 4:
                continue
            for anterior_inicio in range(max(0, atual_inicio - 5), atual_inicio):
                for tamanho_anterior in range(1, min(3, atual_inicio - anterior_inicio) + 1):
                    anterior_fim = anterior_inicio + tamanho_anterior
                    anterior = _texto_janela(segmentos, anterior_inicio, anterior_fim)
                    palavras_anterior = _palavras_significativas(anterior)
                    if len(palavras_anterior) < 4:
                        continue
                    jaccard = _similaridade(palavras_anterior, palavras_atual)
                    sequencia = SequenceMatcher(None, anterior.lower(), atual.lower()).ratio()
                    distancia = float(segmentos[atual_inicio]["start"]) - float(segmentos[anterior_fim - 1]["end"])
                    if distancia > 75 or (jaccard < .48 and sequencia < .58):
                        continue
                    chave = (atual_inicio, atual_fim)
                    if chave in vistos:
                        continue
                    vistos.add(chave)
                    sinal_refaz = bool(_SINAIS_REFAZ.search(atual))
                    titulo = "POSSIVEL REFACAO: explicacao reiniciada" if sinal_refaz else "REPETICAO: explicacao semelhante"
                    descricao = (
                        "Ha sinal de autocorrecao ou reexplicacao seguido de conteudo semelhante. Confira a primeira versao."
                        if sinal_refaz else
                        "A mesma ideia aparece novamente em uma janela proxima. Confirme se e reforco didatico intencional."
                    )
                    achado = _achado("repeticao", float(segmentos[atual_inicio]["start"]), titulo,
                                      descricao, float(segmentos[atual_fim - 1]["end"]))
                    achado["evidence"] = {"antes": anterior[:500], "depois": atual[:500]}
                    achados.append(achado)
                    break
                if (atual_inicio, atual_fim) in vistos:
                    break
    return achados


def _revisar_repeticoes_com_ia(segmentos: list[dict]) -> dict:
    """Segunda opiniao para candidatos locais; falha de IA nao bloqueia auditoria."""
    limpos = [dict(item, text=str(item.get("text", "")).strip()) for item in segmentos
              if str(item.get("text", "")).strip()]
    candidatos = _detectar_repeticoes_locais(limpos)[:12]
    if not candidatos:
        return {"status": "not_needed", "findings": []}
    try:
        from editorial_ai import (
            LIMITE_CHAMADAS_REMOTAS_POR_JOB,
            limitar_chamadas_remotas,
            solicitar_json_textual_com_fallback_ollama,
        )
        contexto = [{"id": indice, "start": item["start"], "end": item["end"], "title": item["title"],
                     "evidence": item.get("evidence", {})}
                    for indice, item in enumerate(candidatos)]
        prompt = (
            "Voce revisa videoaulas. Avalie alertas de fala e nao sugira cortes automaticos. "
            "Retorne JSON: {\"confirmar\":[{\"id\":0,\"tipo\":\"refacao|repeticao|didatica\",\"motivo\":\"maximo 18 palavras\"}]}.\n"
            + json.dumps(contexto, ensure_ascii=False)
        )
        # Uma falha temporaria do Gemini nao pode impedir Gemma e GPT-OSS.
        # So depois de toda a cadeia remota o Ollama entra como ultima opiniao.
        with limitar_chamadas_remotas(True, limite=max(10, LIMITE_CHAMADAS_REMOTAS_POR_JOB)):
            resposta_texto, origem = solicitar_json_textual_com_fallback_ollama(prompt)
            resposta = json.loads(resposta_texto)
        por_id = {item["id"]: item for item in resposta.get("confirmar", []) if isinstance(item, dict)}
        achados = []
        for indice, candidato in enumerate(candidatos):
            avaliacao = por_id.get(indice)
            if not avaliacao or avaliacao.get("tipo") == "didatica":
                continue
            tipo = str(avaliacao.get("tipo", "repeticao"))
            motivo = str(avaliacao.get("motivo", "Confirme a segunda versao da explicacao."))[:180]
            titulo = "IA: POSSIVEL REFACAO" if tipo == "refacao" else "IA: REPETICAO POSSIVEL"
            achados.append(_achado("repeticao", candidato["start"], titulo, motivo, candidato["end"]))
        return {"status": f"completed_{origem}", "findings": achados}
    except Exception as exc:
        logger.warning("Auditoria IA: revisao de repeticoes indisponivel; alertas locais mantidos | %s: %s",
                       type(exc).__name__, exc)
        return {"status": "unavailable", "findings": []}


def _auditar_audio(media_path: str, cuts: list[float]) -> list[dict]:
    if not cuts:
        return []
    with _audio_temporario(media_path) as audio:
        with wave.open(audio, "rb") as wav:
            rate = wav.getframerate()
            sinal = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
    achados = []
    for cut in cuts:
        antes, depois = _energia_lados(sinal, rate, cut)
        if min(antes, depois) > 1e-5 and abs(20 * np.log10((depois + 1e-8) / (antes + 1e-8))) >= 10:
            achados.append(_achado("audio", cut, "ÁUDIO: mudança brusca de volume",
                "O nível de áudio muda 10 dB ou mais na emenda. Ouça e aplique ganho/fade se necessário."))
        inicio, fim = max(0, int((cut - .12) * rate)), min(len(sinal), int((cut + .12) * rate))
        if fim > inicio and np.mean(np.abs(sinal[inicio:fim]) > .99) > .005:
            achados.append(_achado("audio", cut, "ÁUDIO: possível clipping", "Há amostras próximas do limite digital na emenda."))
    return achados


def _energia_lados(sinal: np.ndarray, rate: int, cut: float) -> tuple[float, float]:
    frame = int(cut * rate)
    width = max(1, int(.35 * rate))
    left, right = sinal[max(0, frame - width):frame], sinal[frame:min(len(sinal), frame + width)]
    return tuple(float(np.sqrt(np.mean(part ** 2))) if len(part) else 0.0 for part in (left, right))


def _auditar_imagem(media_path: str, cuts: list[float], fps: float) -> list[dict]:
    achados = []
    for cut in cuts:
        before = _frame(media_path, max(0, cut - 1 / fps))
        after = _frame(media_path, cut + 1 / fps)
        if before is None or after is None:
            continue
        if float(after.mean()) < 6:
            achados.append(_achado("imagem", cut, "IMAGEM: frame escuro", "O primeiro frame apos o corte esta praticamente preto."))
        # Frame muito semelhante no mesmo ponto nao e defeito sozinho, mas
        # reforca que o jump cut deve receber revisao humana.
        if float(np.mean(np.abs(before.astype(np.int16) - after.astype(np.int16)))) < 1.5:
            achados.append(_achado("imagem", cut, "IMAGEM: possível frame congelado", "Frames antes e depois da emenda sao quase idênticos; confirme se nao ha congelamento."))
    return achados


def _frame(media_path: str, second: float) -> np.ndarray | None:
    command = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{second:.4f}", "-i", media_path,
               "-frames:v", "1", "-vf", "scale=160:90,format=gray", "-f", "rawvideo", "-"]
    result = subprocess.run(command, capture_output=True, check=False)
    expected = 160 * 90
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(90, 160) if len(result.stdout) == expected else None


class _audio_temporario:
    def __init__(self, media_path: str):
        self.media_path, self.path = media_path, None

    def __enter__(self) -> str:
        fd, self.path = tempfile.mkstemp(prefix="auditoria_", suffix=".wav")
        os.close(fd)
        command = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", self.media_path,
                   "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", self.path]
        try:
            subprocess.run(command, capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            self.__exit__(None, None, None)
            raise ValueError("Nao foi possivel analisar o audio do video final.") from exc
        return self.path

    def __exit__(self, *_args) -> None:
        if self.path:
            try: os.unlink(self.path)
            except FileNotFoundError: pass
