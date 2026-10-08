"""Curadoria local e avaliacao de cortes. Nao modifica modelos nem timelines."""

import json
import logging
import os
import re
from datetime import datetime, timezone
from threading import RLock

import learning_feedback as feedback

logger = logging.getLogger(__name__)
LOCK = RLock()
REASONS = {
    "erro_fala": "Erro de fala",
    "retake": "Tomada substituida por regravacao",
    "bastidor": "Conversa de bastidor",
    "conteudo_util": "Conteudo util ou repeticao didatica",
    "duracao": "Reducao de duracao",
    "preferencia_projeto": "Preferencia deste projeto",
    "ajuste_limite": "Ajuste fino do limite do corte",
    "outro": "Outro motivo",
}


def _path(record_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", record_id):
        raise ValueError("Identificador de feedback invalido.")
    return feedback.DATA_DIR / f"feedback_{record_id}.json"


def ler_registro(record_id: str) -> dict:
    with LOCK:
        registro = json.loads(_path(record_id).read_text(encoding="utf-8"))
    registro["test_record"] = _teste(registro)
    registro["needs_reimport"] = registro.get("schema_version") != 3
    return registro


def _teste(registro: dict) -> bool:
    return any("pytest" in str(registro.get(campo, "")).lower() for campo in ("suggested_xml", "revised_xml", "diagnostic_path"))


def listar_banco() -> dict:
    registros, ignorados, invalidos = [], 0, 0
    for arquivo in sorted(feedback.DATA_DIR.glob("feedback_*.json")):
        try:
            registro = json.loads(arquivo.read_text(encoding="utf-8"))
            if _teste(registro):
                ignorados += 1
                continue
            exemplos = registro.get("examples", [])
            grupo = registro.get("source_group") or os.path.normcase(registro.get("suggested_xml", ""))
            registros.append({
                "id": registro["id"], "created_at": registro.get("created_at"),
                "project_type": registro.get("project_type"), "source_group": grupo,
                "suggested_xml": registro.get("suggested_xml"), "revised_xml": registro.get("revised_xml"),
                "examples": len(exemplos),
                "approved": sum(e.get("status") == "approved" for e in exemplos),
                "pending": sum(e.get("status", "pending") == "pending" for e in exemplos),
                "dataset_role": registro.get("dataset_role", "reference"),
                "needs_reimport": registro.get("schema_version") != 3,
                "eligible": bool(registro.get("eligible")),
            })
        except (OSError, ValueError, KeyError, TypeError):
            invalidos += 1
    for registro in registros:
        registro["related_versions"] = sum(r["source_group"] == registro["source_group"] for r in registros) - 1
    return {"records": registros, "excluded_test_records": ignorados, "invalid_records": invalidos,
            "automatic_learning": False, "reasons": REASONS}


def anotar(record_id: str, example_ids: list[str], status: str, reason: str | None, note: str = "") -> dict:
    if status not in ("approved", "excluded", "pending"):
        raise ValueError("Estado de revisao invalido.")
    if status == "approved" and reason not in REASONS:
        raise ValueError("Escolha o motivo antes de confirmar.")
    if len(note) > 2000:
        raise ValueError("Observacao limitada a 2000 caracteres.")
    with LOCK:
        registro = ler_registro(record_id)
        if registro["test_record"] or registro["needs_reimport"]:
            raise ValueError("Registro antigo ou de teste: importe novamente os XMLs reais para validar as fontes e recuperar contexto.")
        if status == "approved" and not registro.get("eligible"):
            raise ValueError("Faltam fontes validadas, diagnostico ou tipo de projeto. Este registro so pode ser consultado ou excluido.")
        escolhidos = set(example_ids)
        if not escolhidos or not escolhidos <= {e["id"] for e in registro["examples"]}:
            raise ValueError("Selecione exemplos existentes.")
        agora = datetime.now(timezone.utc).isoformat()
        for exemplo in registro["examples"]:
            if exemplo["id"] in escolhidos:
                exemplo.update(status=status, reason=reason if status == "approved" else None,
                               note=note.strip(), reviewed_at=agora)
        _salvar(registro)
    logger.info("Feedback %s: %d exemplo(s) revisado(s).", record_id, len(escolhidos))
    return registro


def definir_papel(record_id: str, role: str) -> dict:
    if role not in ("reference", "benchmark", "archived"):
        raise ValueError("Papel invalido.")
    with LOCK:
        registro = ler_registro(record_id)
        if registro["test_record"] or registro["needs_reimport"] or not registro.get("eligible"):
            raise ValueError("Reimporte e valide este registro antes de reservar uma avaliacao.")
        grupo = registro["source_group"]
        relacionados = [r for r in listar_banco()["records"] if r["source_group"] == grupo and r["id"] != record_id]
        if role != "archived" and any(r["dataset_role"] not in (role, "archived") for r in relacionados):
            raise ValueError("O mesmo material nao pode servir de referencia e avaliacao. Arquive a outra versao primeiro.")
        registro["dataset_role"] = role
        _salvar(registro)
    return registro


def _salvar(registro: dict) -> None:
    destino = _path(registro["id"])
    temporario = destino.with_suffix(".tmp")
    temporario.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(destino)


def avaliar(record_id: str, candidate_xml: str) -> dict:
    """Compara dois resultados contra a mesma edicao humana, sem chamar APIs."""
    registro = ler_registro(record_id)
    if registro.get("dataset_role") != "benchmark" or not registro.get("eligible"):
        raise ValueError("Escolha um registro validado e reservado para avaliacao.")
    original, final = registro["suggested_xml"], registro["revised_xml"]
    base = feedback.comparar_xmls(original, final)
    if base != registro["comparison"]:
        raise ValueError("Os XMLs de referencia mudaram desde o registro. Reimporte a comparacao antes de avaliar.")
    novo = feedback.comparar_xmls(candidate_xml, final, xml_origem=original)
    if not novo["validated_sources"]:
        raise ValueError("O candidato nao tem fontes identificadas.")
    campos = ("useful_removed_seconds", "unwanted_kept_seconds")
    delta = {k: round(novo["metrics"][k] - base["metrics"][k], 3) for k in campos}
    return {"baseline": base["metrics"], "candidate": novo["metrics"], "delta_seconds": delta,
            "interpretation": "Valores negativos indicam menos divergencia da edicao humana. Nao medem, sozinhos, qualidade editorial.",
            "useful_content_regressed": delta["useful_removed_seconds"] > 0,
            "automatic_learning": False}
