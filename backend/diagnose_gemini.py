"""Teste isolado, com uma unica chamada e sem enviar midia ou credenciais ao log."""

import argparse
import json
import time

import editorial_ai as ai


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=ai.GEMINI_MODEL)
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--transport', choices=('rest', 'sdk'), default='rest')
    args = parser.parse_args()
    payload = {
        'contents': [{'parts': [{'text': 'Return JSON with ok true.'}]}],
        'generationConfig': {
            'responseMimeType': 'application/json',
            'maxOutputTokens': 256,
            'thinkingConfig': {'thinkingLevel': 'low'},
        },
    }
    print(json.dumps({'model': args.model, 'attempts': 1}), flush=True)
    start = time.monotonic()
    try:
        if args.transport == 'sdk':
            from google import genai
            from google.genai import types
            with genai.Client(api_key=ai.GEMINI_API_KEY, http_options=types.HttpOptions(
                timeout=args.timeout * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            )) as client:
                response = client.models.generate_content(
                    model=args.model, contents='Return JSON with ok true.',
                    config=types.GenerateContentConfig(
                        response_mime_type='application/json', max_output_tokens=256,
                        thinking_config=types.ThinkingConfig(thinking_level='low'),
                    ),
                )
                result = response.model_dump(mode='json', exclude_none=True)
        else:
            result = ai._request_url(
                'POST', f'{ai.GEMINI_URL}/{args.model}:generateContent', payload,
                {'x-goog-api-key': ai.GEMINI_API_KEY}, timeout=args.timeout,
            )
        print(json.dumps(result, ensure_ascii=True), flush=True)
    except Exception as exc:
        print(json.dumps({'error': ai._detalhe_erro_remoto(exc)}, ensure_ascii=True), flush=True)
        raise SystemExit(1)
    finally:
        print(json.dumps({'seconds': round(time.monotonic() - start, 2)}), flush=True)


if __name__ == '__main__':
    main()
