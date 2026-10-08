import json
from pathlib import Path

import pytest

from retake_review import candidatos, revisar
from editorial_ai import _validar_indices_resposta, EditorialDecision
from logic_engine import _validar_decisao_semantica


def abertura():
    return json.loads((Path(__file__).parent / 'fixtures/retake_abertura_real.json').read_text(encoding='utf-8'))


def test_abertura_real_inclui_primeira_tomada_e_reinicio_apos_122_segundos():
    s = abertura()
    pares = candidatos(s)
    assert len(pares) == 1
    assert any(0 in p['before'] and any(s[i]['start'] == 122.21 for i in p['after'])
               and p['trigger'] == 'reinicio' for p in pares)


def test_revisao_real_converte_bloco_inteiro_em_cortes_sem_apagar_substituta():
    s = abertura()
    fim_antigo = max(i for i, item in enumerate(s) if item['end'] <= 72.14)
    inicio_novo = next(i for i, item in enumerate(s) if item['start'] == 122.21)
    def responder(prompt):
        dados = json.loads(prompt.split('\n')[-1])
        if 0 in dados['before_ids'] and inicio_novo + 10 in dados['after_ids']:
            return json.dumps(dict(action='replace', old_start=0, old_end=fim_antigo,
                                   new_start=inicio_novo, new_end=inicio_novo+10,
                                   evidence='Interrupcao seguida de contagem e nova abertura reformulada.'))
        return '{"action":"keep"}'
    cortes, _, registros = revisar(s, responder)
    assert set(range(fim_antigo + 1)) <= cortes.keys()
    assert inicio_novo not in cortes
    assert any(r.get('action') == 'replace' for r in registros)


@pytest.mark.parametrize('indice', [661, 2706, -1, True, '1', 1.5])
def test_rejeita_ids_invalidos_mesmo_com_json_valido(indice):
    with pytest.raises(ValueError):
        _validar_indices_resposta({'discard': [{'i': indice, 'reason': 'erro'}]}, set(range(661)))


def test_falha_de_revisao_preserva_fala_e_marca_pendencia():
    def falhar(_):
        raise RuntimeError('offline')
    cortes, revisoes, _ = revisar(abertura(), falhar)
    assert not cortes
    assert revisoes


def test_repeticao_didatica_decidida_keep_nao_e_cortada():
    cortes, revisoes, _ = revisar(abertura(), lambda _: '{"action":"keep"}')
    assert not cortes
    assert not revisoes


def test_limites_inventados_nao_cortam():
    cortes, revisoes, _ = revisar(abertura(), lambda _: json.dumps(dict(
        action='replace', old_start=0, old_end=9999, new_start=10000, new_end=10001, evidence='x')))
    assert not cortes
    assert revisoes


def test_preserva_review_junto_com_cortes_validos():
    d = _validar_decisao_semantica([{}, {}, {}], EditorialDecision({0}, {0: 'erro'}, {2}, {2: 'possivel_retake'}))
    assert d.review_indexes == {2}
    assert d.review_reasons == {2: 'possivel_retake'}
