"""Reavalia um render usando sua transcricao salva e exporta em outra pasta."""
import argparse
import json
from pathlib import Path

from editorial_ai import solicitar_json_textual_com_fallback_ollama
from opening_highlights import criar_candidatos, selecionar_abertura, validar_trechos
from media_utils import obter_metadados, extrair_audio_temporario, aparar_silencios_das_bordas
from xml_generator import gerar_fcp_xml_abertura


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('transcript')
    parser.add_argument('output')
    parser.add_argument('--reuse-screening', action='store_true')
    args = parser.parse_args()
    dados = json.loads(Path(args.transcript).read_text(encoding='utf-8'))
    segmentos = dados['transcript']
    fonte = dados['source']
    destino = Path(args.output)
    destino.mkdir(parents=True, exist_ok=True)
    print('Segmentos:', len(segmentos), 'Candidatos:', len(criar_candidatos(segmentos)), flush=True)
    chamadas = []
    anteriores = json.loads((destino / 'curadoria.json').read_text(encoding='utf-8')) if args.reuse_screening else []

    def solicitar(prompt):
        print('Curadoria: chamada', len(chamadas) + 1, flush=True)
        indice = len(chamadas)
        if prompt.startswith('Avalie os takes') and indice < len(anteriores):
            texto, origem = anteriores[indice]['response'], anteriores[indice]['origin']
        else:
            texto, origem = solicitar_json_textual_com_fallback_ollama(prompt)
        chamadas.append({'origin': origem, 'response': texto})
        (destino / 'curadoria.json').write_text(json.dumps(chamadas, ensure_ascii=False, indent=2), encoding='utf-8')
        return texto

    trechos = selecionar_abertura(segmentos, solicitar)
    midia = obter_metadados(fonte)
    with extrair_audio_temporario(fonte, midia.get('video_start', 0)) as audio:
        trechos = aparar_silencios_das_bordas(audio, trechos)
    validar_trechos(trechos)
    total = sum(t['end'] - t['start'] for t in trechos)
    if not 30 <= total <= 40:
        raise ValueError(f'Duracao final invalida: {total}')
    gerar_fcp_xml_abertura(trechos, dict(midia, name=Path(fonte).name), str(destino / 'abertura_revisada.xml'), midia['fps'])
    (destino / 'abertura_revisada.json').write_text(json.dumps({'source': fonte, 'duration': total, 'highlights': trechos}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'duration': total, 'highlights': trechos}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
