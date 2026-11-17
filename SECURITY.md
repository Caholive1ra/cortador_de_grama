# Segurança

## Credenciais

Este repositório não aceita chaves de API, tokens, senhas ou certificados.

- Guarde credenciais exclusivamente em `.env` ou em variáveis de ambiente locais.
- Comece sempre por `.env.example`, que contém apenas valores de preenchimento.
- Não inclua o prefixo `Bearer ` em `NVIDIA_API_KEY`; o backend monta o cabeçalho de autenticação.
- Nunca cole uma chave em issue, pull request, mensagem, captura de tela ou documentação.

Os arquivos `.env`, `.env.*` (exceto `.env.example`), certificados e os principais artefatos gerados estão listados no `.gitignore`.

## Se uma chave for exposta

1. Revogue imediatamente a chave no painel do provedor.
2. Crie uma chave nova e atualize apenas o arquivo `.env` local.
3. Se houve commit, remova a credencial de todo o histórico antes de tornar o repositório público.
4. Verifique o que será enviado com `git status` e `git diff --cached --check`.

Revogar a chave é indispensável: apagar o texto de um arquivo não a remove de commits já publicados.

## Relato responsável

Para relatar uma falha de segurança, não abra uma issue pública com detalhes ou credenciais. Entre em contato diretamente com a pessoa responsável pelo projeto e envie apenas as informações necessárias para reproduzir o problema com segurança.
