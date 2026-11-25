# Tutorial: instalar o Assistente de Decupagem no macOS

Este guia prepara uma segunda maquina Mac para rodar o backend local e o painel UXP do Adobe Premiere. Nao e necessario configurar Supabase, Render ou GitHub Actions para cortar, gerar abertura, lettering ou auditoria localmente.

## Antes de comecar

Voce precisa de:

- Mac com macOS atualizado e pelo menos 16 GB de RAM recomendados;
- Adobe Premiere Pro **25.6 ou superior**;
- permissao de administrador no Mac para instalar ferramentas e habilitar o modo de desenvolvimento UXP;
- acesso ao repositorio do projeto;
- aproximadamente 15 GB livres para ambiente, modelos, cache e arquivos temporarios. Reserve mais espaco se os videos forem grandes;
- opcionalmente, chaves Gemini e NVIDIA. Sem elas, o sistema ainda funciona com o Ollama local, mas a revisao remota nao sera usada.

> Macs Apple Silicon (M1, M2, M3, M4 e posteriores) **nao usam CUDA**. Neste projeto, a transcricao com `faster-whisper` roda em CPU/int8 no macOS. Nao instale DLLs, CUDA ou cuDNN do tutorial Windows.

## 1. Descobrir se o Mac e Apple Silicon ou Intel

1. Clique no icone Apple no canto superior esquerdo.
2. Escolha **Sobre Este Mac**.
3. Leia o campo **Chip**:
   - `Apple M...`: Apple Silicon;
   - `Intel...`: Mac Intel.

Os dois funcionam. O Apple Silicon normalmente transcreve mais rapido e usa menos energia.

## 2. Abrir o Terminal e instalar as ferramentas basicas

1. Abra o Spotlight com `Command + Espaco`.
2. Digite **Terminal** e pressione Enter.
3. Instale as ferramentas de linha de comando da Apple:

```bash
xcode-select --install
```

4. Quando a janela aparecer, clique em **Instalar** e espere terminar. Se o Terminal disser que elas ja existem, siga adiante.
5. Instale o Homebrew. Ele instalara Python e FFmpeg:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Leia as instrucoes mostradas no final. Em Macs Apple Silicon, normalmente o instalador pedira que voce copie dois comandos `echo`/`eval` para ativar o Homebrew. Execute exatamente os comandos que ele mostrar.

6. Feche e abra o Terminal novamente. Confirme:

```bash
brew --version
```

Se aparecer uma versao, esta etapa terminou. A documentacao oficial explica os caminhos diferentes para Apple Silicon e Intel: [Homebrew](https://brew.sh/).

## 3. Instalar Python 3.11 e FFmpeg

No Terminal, execute:

```bash
brew install python@3.11 ffmpeg
python3.11 --version
ffmpeg -version
ffprobe -version
```

Os tres ultimos comandos devem mostrar versoes, sem erro. Este projeto recomenda Python 3.11 para manter compatibilidade previsivel. Nao use o Python que vem com o macOS (`/usr/bin/python3`) para o projeto.

Se `python3.11` nao for encontrado, rode:

```bash
brew link --overwrite python@3.11
```

Depois, feche e abra o Terminal e tente `python3.11 --version` novamente.

## 4. Baixar o projeto

Escolha uma pasta sua, por exemplo `Documentos`. No Terminal:

```bash
cd ~/Documents
git clone https://github.com/Caholive1ra/cortador_de_grama.git
cd cortador_de_grama
git switch feat/aprendizado-editorial-cloud
```

Se o projeto ja existir, em vez de clonar:

```bash
cd ~/Documents/cortador_de_grama
git pull
git switch feat/aprendizado-editorial-cloud
```

Use a branch que a equipe indicar. Quando houver merge para `main`, troque o ultimo comando por `git switch main`.

## 5. Criar o ambiente Python e instalar dependencias

Ainda na pasta do projeto, execute cada linha e espere terminar:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
```

O inicio da linha do Terminal deve passar a mostrar `(.venv)`. Isso confirma que voce esta usando o ambiente do projeto.

O arquivo de dependencias ignora automaticamente os pacotes CUDA no macOS. Eles pertencem apenas ao Windows.

Teste a instalacao:

```bash
python -m pytest backend -q
```

O esperado e todos os testes passarem. Se um teste falhar, anote a primeira mensagem de erro; nao copie nem envie chaves de API.

## 6. Instalar e preparar o Ollama local

O Ollama e a ultima alternativa local para analises textuais. Ele permite que a ferramenta continue entregando resultado quando as APIs remotas estiverem indisponiveis.

1. Acesse [ollama.com/download/mac](https://ollama.com/download/mac).
2. Baixe a versao para macOS e mova **Ollama.app** para **Aplicativos** se o instalador pedir.
3. Abra **Ollama** em Aplicativos. Autorize a abertura se o macOS perguntar.
4. Volte ao Terminal e baixe o modelo configurado pelo projeto:

```bash
ollama pull qwen2.5:3b
ollama list
```

Na lista, deve aparecer `qwen2.5:3b`.

Teste o servidor local:

```bash
curl http://127.0.0.1:11434/api/tags
```

O resultado deve conter JSON e o nome do modelo. Se der `Connection refused`, abra o Ollama.app e espere alguns segundos antes de tentar novamente.

## 7. Criar o arquivo de configuracao `.env`

Ainda dentro da pasta do projeto:

```bash
cp .env.example .env
open -e .env
```

O TextEdit abrira o arquivo. Preencha somente as chaves que sua equipe autorizou. Exemplo:

```dotenv
GEMINI_API_KEY="sua_chave_gemini"
NVIDIA_API_KEY="sua_chave_nvidia"
```

Se voce nao tiver chaves, deixe as linhas vazias. O Ollama continuara disponivel como fallback local.

Nao coloque `Bearer ` antes da chave NVIDIA. Nunca envie o arquivo `.env` por chat, e-mail, Git ou print. Ele fica fora do versionamento.

Para confirmar que o arquivo existe sem revelar o conteudo:

```bash
test -f .env && echo "Arquivo .env encontrado"
```

## 8. Iniciar e testar o backend

Use um Terminal exclusivamente para o backend:

```bash
cd ~/Documents/cortador_de_grama
source .venv/bin/activate
python backend/main.py
```

Deixe esta janela aberta. Quando aparecer que o servidor iniciou na porta `8000`, abra **outro** Terminal e execute:

```bash
curl http://127.0.0.1:8000/health
```

O esperado e uma resposta JSON de saude. Para interromper o backend mais tarde, volte a primeira janela e pressione `Control + C`.

Diagnosticos que nao mostram chaves:

```bash
curl http://127.0.0.1:8000/diagnostics/gemini
curl http://127.0.0.1:8000/diagnostics/nvidia
```

Logs locais:

```bash
open backend/logs
```

## 9. Instalar o painel no Adobe Premiere

1. Abra o aplicativo **Adobe Creative Cloud**.
2. Em **Apps**, procure por **UXP Developer Tools** e clique em **Instalar**.
3. Abra o **Adobe UXP Developer Tool** em Aplicativos.
4. Na primeira abertura, aceite **Enable Developer Mode** e informe a senha de administrador quando o macOS solicitar.
5. Abra o Adobe Premiere Pro 25.6 ou superior.
6. No UXP Developer Tool, adicione o plugin apontando para esta pasta:

```text
~/Documents/cortador_de_grama/premiere_plugin
```

7. Carregue o plugin no Premiere pelo UXP Developer Tool.
8. No Premiere, abra o painel **Assistente de Decupagem** pelo menu de plugins/janelas UXP.

O painel se comunica somente com `http://localhost:8000`; o backend nao fica exposto na rede. A Adobe documenta que Premiere 25.6+ suporta UXP e que o UXP Developer Tool requer modo de desenvolvimento e privilegios administrativos: [ferramentas UXP para Premiere](https://developer.adobe.com/premiere-pro/uxp/introduction/essentials/dev-tools/).

## 10. Primeiro teste seguro

1. Confirme que Ollama e backend estao abertos.
2. Abra um projeto de teste no Premiere.
3. Carregue o painel.
4. Escolha um video curto, sem dados sensiveis, e use a tarefa de corte.
5. Ao concluir, confira o popup e o arquivo `*_diagnostico_*.json` criado proximo ao video.
6. Importe o XML no Premiere e revise inicio/fim de frases, silencios e refacoes antes de usar material em producao.

## Atualizacao futura

Pare o backend com `Control + C`, entao execute:

```bash
cd ~/Documents/cortador_de_grama
git pull
source .venv/bin/activate
pip install -r backend/requirements.txt
python -m pytest backend -q
python backend/main.py
```

No UXP Developer Tool, use **Reload** no plugin depois de atualizacoes em `premiere_plugin`.

## Problemas comuns

| Problema | O que fazer |
| --- | --- |
| `brew: command not found` | Feche e abra o Terminal. Se persistir, execute os comandos de ativacao exibidos ao fim da instalacao do Homebrew. |
| `python3.11: command not found` | Rode `brew link --overwrite python@3.11`, reabra o Terminal e teste novamente. |
| `ffmpeg: command not found` | Rode `brew install ffmpeg` e reabra o Terminal. |
| `Connection refused` na porta 11434 | Abra Ollama.app e confirme `ollama list`. |
| `Connection refused` na porta 8000 | Ative `.venv` e inicie `python backend/main.py`; mantenha esse Terminal aberto. |
| Painel nao aparece no Premiere | Confirme Premiere 25.6+, Developer Mode habilitado e plugin carregado pelo UXP Developer Tool. Use Reload. |
| O painel diz que backend esta indisponivel | Teste `curl http://127.0.0.1:8000/health`. Se responder, recarregue o painel UXP. |
| Instalacao do `pip` falha | Confirme `python3.11 --version`, atualize `pip` e guarde a primeira linha de erro. Nao tente instalar CUDA no Mac. |
| Transcricao esta lenta | Em Mac, ela usa CPU. Feche aplicativos pesados, use arquivos locais em SSD e comece por um video curto para validar o fluxo. |
| macOS bloqueia um app | Abra **Ajustes do Sistema > Privacidade e Seguranca** e autorize somente o aplicativo que voce acabou de instalar e reconhece. |

## Checklist final

- [ ] `brew --version` funciona.
- [ ] `python3.11 --version`, `ffmpeg -version` e `ffprobe -version` funcionam.
- [ ] `.venv` foi criado e `pip install -r backend/requirements.txt` terminou.
- [ ] `python -m pytest backend -q` passou.
- [ ] `ollama list` mostra `qwen2.5:3b`.
- [ ] `curl http://127.0.0.1:8000/health` responde JSON.
- [ ] O painel foi carregado pelo UXP Developer Tool.
- [ ] Um video curto foi processado e o XML foi revisado no Premiere.

Se alguma etapa falhar, registre o comando executado, a primeira mensagem de erro, a versao do macOS e se o Mac e Apple Silicon ou Intel. Nunca inclua chaves de API, `.env`, videos ou transcricoes confidenciais no chamado.
