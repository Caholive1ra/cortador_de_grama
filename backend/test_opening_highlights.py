import xml.etree.ElementTree as ET
import pytest

from opening_highlights import selecionar_abertura, criar_candidatos, validar_trechos, _prompt
from xml_generator import gerar_fcp_xml_abertura


def _segmento(inicio, texto):
    return {"start": inicio, "end": inicio + 9, "text": texto}


def test_junta_frase_dividida_pelo_whisper_e_descarta_final_incompleto():
    candidatos = criar_candidatos([
        {"start": 0, "end": 3, "text": "O maior erro foi acreditar que"},
        {"start": 3, "end": 8, "text": "o dinheiro resolveria todos os problemas da empresa."},
        {"start": 8, "end": 12, "text": "Mas quando nós"},
    ])
    assert len(candidatos) == 1
    assert candidatos[0]["text"].startswith("O maior erro")
    assert candidatos[0]["end"] == 8
    assert "Mas quando" not in candidatos[0]["text"]


def test_candidatos_da_abertura_priorizam_frases_curtas_e_nao_explicacoes_longas():
    candidatos = criar_candidatos([
        {"start": 0, "end": 4, "text": "Não é a gente que está indo para o futuro."},
        {"start": 4, "end": 8, "text": "É o futuro que está vindo até a gente."},
        {"start": 20, "end": 33, "text": "Esta explicação longa não deve caber como um único take da abertura."},
    ])
    assert len(candidatos) == 3
    assert all("explicação longa" not in c["text"] for c in candidatos)
    assert all(c["duration"] <= 10.5 for c in candidatos)
    assert "FRASES DE IMPACTO" in _prompt(candidatos)
    assert "Não é a gente que está indo" in _prompt(candidatos)


def test_perguntas_orientam_contexto_sem_virarem_fala_da_abertura():
    candidatos = criar_candidatos([
        {"start": 0, "end": 5, "text": "O que mudou com a inteligência artificial?"},
        {"start": 5, "end": 10, "text": "O futuro já está chegando antes de a gente se preparar."},
    ])
    prompt = _prompt(candidatos, "Bloco 1: o que mudou? Bloco 2: como reagir?")
    assert "PERGUNTAS DOS BLOCOS" in prompt
    assert "NUNCA selecione essas perguntas" in prompt
    assert candidatos[0]["speaker_hint"] == "possível pergunta ou ponte do apresentador"
    assert candidatos[-1]["speaker_hint"] == "possível fala de convidado"


def test_bloqueia_ids_diferentes_com_audio_sobreposto():
    with pytest.raises(ValueError, match="sobrepostos"):
        validar_trechos([
            {"start": 10, "end": 20, "text": "Uma pergunta sobre os riscos."},
            {"start": 15, "end": 25, "text": "Uma resposta sobre as oportunidades."},
        ])


def test_bloqueia_mesma_fala_gravada_em_outro_momento():
    with pytest.raises(ValueError, match="repetidas"):
        validar_trechos([
            {"start": 10, "end": 20, "text": "O preço da escolha foi perder toda a equipe."},
            {"start": 100, "end": 110, "text": "O preço da escolha foi perder toda a equipe!"},
        ])


def test_nao_apara_pausa_interna_se_a_fala_retoma_antes_do_fim(monkeypatch):
    from types import SimpleNamespace
    from media_utils import aparar_silencios_das_bordas
    monkeypatch.setattr("media_utils.subprocess.run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stderr="silence_start: 9.6\nsilence_end: 9.8"))
    trecho = {"start": 0, "end": 10, "duration": 10, "text": "Fala."}
    assert aparar_silencios_das_bordas("teste.wav", [trecho])[0]["end"] == 10


def test_selecao_cria_abertura_cronologica_com_falas_de_todo_videocast():
    segmentos = [
        _segmento(0, "Hoje vamos conversar sobre um problema importante que mudou todo o mercado."),
        _segmento(500, "O ponto é que essa decisão cria uma oportunidade real para quem entende o risco."),
        _segmento(1100, "E no futuro ninguém vai poder ignorar essa diferença essencial."),
        _segmento(1500, "A pergunta é como transformar esse risco em um resultado melhor na prática."),
    ]
    resultado = selecionar_abertura(
        segmentos, lambda _prompt: ('{"selection":[{"id":0,"speaker":"apresentador"},'
                                    '{"id":1,"speaker":"convidado"},{"id":2,"speaker":"convidado"},'
                                    '{"id":3,"speaker":"convidado"}]}')
    )
    assert len(resultado) == 4
    assert [item["start"] for item in resultado] == sorted(item["start"] for item in resultado)
    assert sum(item["speaker"] == "apresentador" for item in resultado) == 1
    assert 30 <= sum(item["duration"] for item in resultado) <= 40


def test_curadoria_com_duracao_invalida_usa_abertura_de_reserva():
    segmentos = [
        _segmento(0, "Uma verdade importante muda o futuro e cria uma oportunidade real para todos."),
        _segmento(100, "O problema é que essa decisão traz risco e exige uma escolha muito importante."),
        _segmento(200, "A diferença essencial é entender o resultado antes de tomar uma decisão crítica."),
    ]
    resultado = selecionar_abertura(segmentos, lambda _prompt: '{"selection":[{"id":0}]}')
    assert len(resultado) >= 3
    assert all(item.get("fallback_local") for item in resultado)


def test_entrega_abertura_de_reserva_quando_modelo_marca_pergunta_como_apresentador():
    segmentos = [
        _segmento(0, "O que mudou no mercado nos últimos anos?"),
        _segmento(100, "A transformação acontece quando a empresa entende o problema real."),
        _segmento(200, "O maior risco é acreditar que a mudança pode esperar."),
        _segmento(300, "Quem aprende cedo transforma incerteza em vantagem concreta."),
        _segmento(400, "O futuro não pede permissão antes de chegar."),
    ]
    resposta_invalida = ('{"selection":[{"id":0,"speaker":"apresentador"},'
                         '{"id":1,"speaker":"convidado"},{"id":2,"speaker":"convidado"},'
                         '{"id":3,"speaker":"convidado"}]}')
    resultado = selecionar_abertura(segmentos, lambda _prompt: resposta_invalida)
    assert len(resultado) >= 4
    assert all(item.get("fallback_local") for item in resultado)
    assert all("O que mudou" not in item["text"] for item in resultado)


def test_entrega_abertura_de_reserva_quando_provedor_falha():
    segmentos = [
        _segmento(indice * 100, texto) for indice, texto in enumerate([
            "A mudança começa quando a gente para de adiar a decisão.",
            "Toda inovação cobra coragem antes de entregar resultado.",
            "O risco maior é repetir o processo que já não funciona.",
            "Uma boa pergunta muda a qualidade de toda escolha.",
        ])
    ]
    resultado = selecionar_abertura(segmentos, lambda _prompt: (_ for _ in ()).throw(ConnectionError("offline")))
    assert len(resultado) >= 4
    assert all(item.get("fallback_local") for item in resultado)


def test_uma_fala_completa_ainda_gera_abertura_de_reserva():
    resultado = selecionar_abertura([
        _segmento(0, "Uma única fala completa ainda deve ser entregue para o editor revisar."),
    ])
    assert len(resultado) == 1
    assert resultado[0].get("fallback_local") is True


def test_abertura_limita_contexto_remoto_e_entrega_reserva_apos_uma_falha():
    segmentos = [
        _segmento(indice * 12, f"Uma ideia completa e memoravel numero {indice} para o episodio.")
        for indice in range(90)
    ]
    prompts = []

    def indisponivel(prompto):
        prompts.append(prompto)
        raise TimeoutError("provedor indisponivel")

    resultado = selecionar_abertura(segmentos, indisponivel, max_tentativas_ia=1)

    assert len(prompts) == 1
    candidatos_enviados = prompts[0].split("Candidatos:\n", 1)[1]
    assert candidatos_enviados.count('"id"') <= 60
    assert resultado
    assert all(item.get("fallback_local") for item in resultado)


def test_xml_abertura_condensa_os_trechos_na_timeline(tmp_path):
    fonte = {"name": "videocast.mp4", "path": str(tmp_path / "videocast.mp4"), "duration": 100,
             "fps": 30, "width": 1920, "height": 1080}
    destino = tmp_path / "abertura.xml"
    gerar_fcp_xml_abertura([
        {"start": 20, "end": 25, "text": "Primeira fala"},
        {"start": 70, "end": 78, "text": "Segunda fala"},
    ], fonte, str(destino), 30)
    raiz = ET.parse(destino).getroot()
    clipes = raiz.findall("./sequence/media/video/track/clipitem")
    assert [(c.findtext("start"), c.findtext("end"), c.findtext("in"), c.findtext("out")) for c in clipes] == [
        ("0", "150", "600", "750"), ("150", "390", "2100", "2340"),
    ]
