# Configuração cloud passo a passo

Este roteiro ativa o aprendizado compartilhado sem enviar vídeos, áudios, XMLs,
transcrições ou caminhos de arquivos dos editores. Nunca cole chaves no chat ou
em commits.

## 1. Criar o projeto Supabase

1. Acesse [Supabase Dashboard](https://supabase.com/dashboard), escolha **New project**, informe organização, nome e região próxima aos editores. (Red22546038)
2. Em **Project settings > API**, copie a **Project URL**. A `service_role` deve ficar somente no worker cloud; não a coloque no plugin ou no computador de edição.
3. Em **SQL Editor > New query**, abra `backend/migrations/001_cloud_learning.sql`, copie o conteúdo, execute **Run** e confirme que não houve erro.
4. Em **Table Editor**, confira as tabelas `editor_feedback`, `learning_jobs`, `prompt_versions`, `evaluation_results` e `deployment_versions`.
5. As tabelas têm RLS ligado e nenhuma policy pública. Não crie policy de `anon` para esses dados. O worker usa a service-role, que ignora RLS, e deve estar hospedado em ambiente protegido.

Teste: execute no SQL Editor `select * from editorial_profiles;`. Devem existir quatro perfis.

## 2. Configurar NVIDIA

1. Acesse a página oficial [Nemotron 3 Ultra 550B](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b), entre em **Build** e gere uma API key.
2. O identificador confirmado para o endpoint hospedado é `nvidia/nemotron-3-ultra-550b-a55b`; a URL é `https://integrate.api.nvidia.com/v1/chat/completions`.
3. O modelo é OpenAI-compatível, mas a página declara que **Structured Output não é suportado**. O worker pede JSON textual e aplica validação rígida antes de criar uma proposta.
4. Guarde a key apenas no provedor de hospedagem como `NVIDIA_LEARNING_API_KEY`.

Teste: não use a key no plugin. Após o deploy do worker, envie um job de teste e confirme em `learning_jobs` o status `completed` ou `failed`, sem segredo no campo `error`.

## 3. Hospedar o worker

Hospede `backend/cloud_worker.py` em um serviço Python com processo web e processo worker separados (Render, Railway, Cloud Run ou equivalente).

Variáveis do ambiente cloud:

```text
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<somente-no-worker>
CLOUD_LEARNING_INSTALLATION_TOKEN=<token-aleatorio-longo>
NVIDIA_LEARNING_API_KEY=<somente-no-worker>
NVIDIA_LEARNING_MODEL=nvidia/nemotron-3-ultra-550b-a55b
NVIDIA_LEARNING_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_LEARNING_MAX_TOKENS=1600
NVIDIA_LEARNING_TIMEOUT_SECONDS=90
CLOUD_WORKER_POLL_SECONDS=30
```

Processo web: `uvicorn cloud_worker:app --host 0.0.0.0 --port $PORT`.
Processo worker: `python cloud_worker.py --worker`.

O endpoint deve ficar atrás de HTTPS. Restrinja firewall/origem conforme o serviço de hospedagem permitir. Para produção, substitua o token compartilhado por tokens individuais/JWT emitidos pelo seu provedor de identidade; o token atual é o mínimo para instalações autorizadas e pode ser rotacionado.

Teste: faça `GET /docs` no domínio do worker. Um `POST /v1/feedback` sem o cabeçalho `X-Installation-Token` deve retornar 401.

## 4. Configurar cada computador de edição

1. Copie `.env.example` para `.env` na raiz do projeto local.
2. Preencha apenas:

```text
CLOUD_LEARNING_ENABLED=true
CLOUD_LEARNING_API_URL=https://<seu-worker>
CLOUD_LEARNING_INSTALLATION_TOKEN=<token-da-instalacao>
```

3. Não preencha `SUPABASE_SERVICE_ROLE_KEY` nem `NVIDIA_LEARNING_API_KEY` na estação local.
4. Reinicie o backend local.
5. No Premiere, abra **Aprender**, selecione XMLs originais e o XML revisado, marque categorias gerais se desejar e envie.

Teste: `GET http://localhost:8000/learning/cloud-status` deve mostrar `configured: true`. Se a internet cair, o feedback continua local e fica na outbox; ao voltar, um novo envio ou reinício sincroniza o pendente.

## 5. Validar dois computadores

1. Configure duas instalações com tokens distintos (recomendado) ou o token inicial rotacionável.
2. No computador A, envie um feedback sem marcar categoria; confirme que ele é aceito e não há conteúdo em `editor_feedback` além de métricas/categorias.
3. No computador B, envie feedback de outro perfil.
4. Execute o worker e confirme jobs em `learning_jobs`.
5. Uma proposta aparece em `prompt_versions` como `evaluation_pending`. Ela **não** é distribuída automaticamente.

## 6. Avaliar, aprovar e rollback

Para promover, reexecute a decupagem com a candidata em benchmarks reservados que tenham mídia/contexto acessível ao ambiente de avaliação. Registre em `evaluation_results`:

- `reexecution_complete=true`;
- ao menos cinco benchmarks independentes;
- não piorar conteúdo útil removido nem conteúdo desnecessário mantido;
- não reduzir concordância.

Sem esses dados, marque a avaliação como `incomplete` e mantenha a versão anterior. Após aprovação, insira uma configuração com `status=approved` em `deployment_versions`. As instalações baixam-na no próximo refresh e usam o cache local durante queda de rede. Para rollback, crie uma nova `deployment_versions` aprovada apontando para a configuração anterior; não apague histórico.

## Erros frequentes

- **401 instalação não autorizada:** confira o token do worker e da instalação; gere/rote os tokens se houve exposição.
- **503 banco indisponível:** confira `SUPABASE_URL`, service-role no worker e logs; a outbox local preserva eventos.
- **Proposta inválida:** esperado quando a IA não respeita o schema; o worker deve marcar job como falho e não publicar nada.
- **Nenhuma configuração nova:** normal até existir benchmark reexecutado e aprovado.
- **A edição ficou lenta:** o aprendizado é assíncrono; revise se o backend local está tentando o worker dentro do corte. O corte deve ler somente cache local.

## Operação em produção

Monitore volume de `learning_jobs`, falhas, duração, tokens em `prompt_versions.telemetry`, custo NVIDIA e tamanho da outbox. Defina orçamento/alertas no provedor, faça backup do Supabase e revise mensalmente configurações aprovadas. Não habilite compartilhamento futuro de textos ou mídia sem revisão jurídica e consentimento explícito.
