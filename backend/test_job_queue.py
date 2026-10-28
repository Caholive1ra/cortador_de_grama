"""Testes da fila local de tarefas pesadas."""

import time

from job_queue import PriorityJobQueue


def _wait_for(queue: PriorityJobQueue, job_id: str) -> dict:
    for _ in range(100):
        job = queue.get(job_id)
        if job and job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("A tarefa nao foi concluida a tempo.")


def test_prioridade_vence_ordem_de_chegada(tmp_path) -> None:
    queue = PriorityJobQueue(tmp_path / "jobs.sqlite3")
    lettering = queue.enqueue("lettering", {"nome": "lettering"}, priority=20)
    corte = queue.enqueue("process", {"nome": "corte"}, priority=0)

    executadas: list[str] = []

    def executar(task: str, payload: dict) -> dict:
        executadas.append(payload["nome"])
        return {"task": task, **payload}

    queue.start(executar)
    assert _wait_for(queue, corte["id"])["result"]["nome"] == "corte"
    assert _wait_for(queue, lettering["id"])["result"]["nome"] == "lettering"
    assert executadas == ["corte", "lettering"]


def test_posicao_reflete_as_tarefas_mais_prioritarias(tmp_path) -> None:
    queue = PriorityJobQueue(tmp_path / "jobs.sqlite3")
    lettering = queue.enqueue("lettering", {}, priority=20)
    auditoria = queue.enqueue("audit", {}, priority=10)
    corte = queue.enqueue("process", {}, priority=0)

    assert queue.get(corte["id"])["queue_position"] == 1
    assert queue.get(auditoria["id"])["queue_position"] == 2
    assert queue.get(lettering["id"])["queue_position"] == 3


def test_interrompida_nao_volta_a_fila_sem_confirmacao(tmp_path) -> None:
    database = tmp_path / "jobs.sqlite3"
    queue = PriorityJobQueue(database)
    job = queue.enqueue("audit", {}, priority=10, title="Auditoria: aula.mp4")
    with queue._connect() as connection:
        connection.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job["id"],))

    restarted = PriorityJobQueue(database)
    interrupted = restarted.get(job["id"])
    assert interrupted["status"] == "interrupted"
    assert "Escolha repetir" in interrupted["error"]["detail"]
    assert restarted.retry(job["id"])["status"] == "queued"


def test_cancelar_so_remove_tarefa_aguardando(tmp_path) -> None:
    queue = PriorityJobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue("lettering", {}, priority=20)
    assert queue.cancel(job["id"])["status"] == "cancelled"


def test_cancelamento_em_andamento_descarta_resultado_com_seguranca(tmp_path) -> None:
    queue = PriorityJobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue("audit", {}, priority=10)
    with queue._connect() as connection:
        connection.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job["id"],))

    requested = queue.cancel(job["id"])
    assert requested["status"] == "running"
    assert requested["cancel_requested"] is True
    queue._finish(job["id"], result={"nao": "usar"})
    finished = queue.get(job["id"])
    assert finished["status"] == "cancelled"
    assert "etapa segura" in finished["error"]["detail"]
