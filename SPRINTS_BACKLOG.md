# BACKLOG DE EXECUÇÃO (CHAIN OF THOUGHT)

## Etapa 1: Backend Base e Transcrição
- [ ] **Tarefa 1.1:** Criar o ambiente Python, `requirements.txt` (fastapi, uvicorn, faster-whisper) e configurar o `main.py` rodando na porta 8000 com uma rota de health-check.
- [ ] **Tarefa 1.2:** Criar o módulo `transcriber.py` que instancie o WhisperModel e tenha uma função que recebe um arquivo `.wav` e retorna uma lista de dicionários `[{start, end, text}]`.
- [ ] **Tarefa 1.3:** Conectar o transcritor a uma rota REST `POST /process` no FastAPI. Adicionar testes unitários simulando a rota.

## Etapa 2: Motor Lógico e Gerador de FCP XML
- [ ] **Tarefa 2.1:** Criar o módulo `logic_engine.py`. Ele deve receber a lista do Whisper e adicionar a chave `track: "V1"` (se o texto tiver palavras-gatilho) ou `track: "V2"` (se for fala limpa).
- [ ] **Tarefa 2.2:** Criar o módulo `xml_generator.py`. Desenvolver a função matemática que converte os `timestamps` decimais em Timecode do Premiere (HH:MM:SS:FF).
- [ ] **Tarefa 2.3:** Escrever a lógica que formata o FCP XML, mapeando os itens categorizados para as trilhas corretas e salvando o arquivo localmente. Testar a geração do XML e tentar importá-lo manualmente no Premiere.

## Etapa 3: Plugin UXP (Frontend & Permissões)
- [ ] **Tarefa 3.1:** Criar a pasta do plugin e o arquivo `manifest.json` v5. Configurar estritamente os `requiredPermissions` para acesso ao `localFileSystem` e comunicação `network` (localhost).
- [ ] **Tarefa 3.2:** Desenvolver o `index.html` com a UI básica do painel da Adobe (cores escuras, botões).
- [ ] **Tarefa 3.3:** Escrever o `main.js` com a função Fetch que envia requisições para o FastAPI e lida com blocos Try/Catch para erros de servidor offline.

## Etapa 4: Integração Premiere API
- [ ] **Tarefa 4.1:** Usar o `require('uxp').storage.localFileSystem` para permitir que o usuário selecione o arquivo bruto via caixa de diálogo (fallback caso a API não consiga ler a timeline).
- [ ] **Tarefa 4.2:** Após o servidor devolver o caminho do XML gerado, escrever o código UXP que executa a importação automática do XML para dentro do Project Bin do Premiere.