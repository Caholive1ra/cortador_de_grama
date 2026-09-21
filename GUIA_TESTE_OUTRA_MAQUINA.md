# Guia de instalacao e teste em outra maquina

Este projeto e composto por duas partes:

- `backend/`: servidor local FastAPI que transcreve o PGM, classifica os trechos e gera o XML.
- `premiere_plugin/`: painel UXP do Premiere que envia os caminhos da midia para o backend e importa o XML gerado.

## 1. O que precisa estar instalado manualmente

Instale estes itens na maquina de teste antes de rodar o projeto:

1. **Windows 10/11**.
2. **Adobe Premiere Pro** compativel com UXP, idealmente versao `25.6.0` ou superior, conforme `premiere_plugin/manifest.json`.
3. **Python 3.11 ou superior** com `pip`.
4. **FFmpeg completo**, contendo `ffmpeg` e `ffprobe` no `PATH`.
5. **UXP Developer Tool** da Adobe, para carregar o plugin local no Premiere.
6. **Git** ou outro metodo para copiar a pasta do projeto para a maquina.

O script deste repositorio instala somente as dependencias Python. Ele nao instala Premiere, UXP Developer Tool, Python nem FFmpeg.

## 2. Copiar o projeto

Na maquina de teste, copie ou clone o projeto para uma pasta local, por exemplo:

```powershell
C:\Projetos\cortador-de-grama
```

Abra o PowerShell nessa pasta raiz. Ela deve conter:

```text
backend/
premiere_plugin/
setup_teste_windows.ps1
GUIA_TESTE_OUTRA_MAQUINA.md
```

## 3. Validar Python e FFmpeg

No PowerShell:

```powershell
python --version
ffmpeg -version
ffprobe -version
```

Se `python` nao funcionar, tente:

```powershell
py --version
```

Se `ffmpeg` ou `ffprobe` nao forem encontrados, instale o FFmpeg e adicione a pasta `bin` ao `PATH` do Windows.

## 4. Instalar dependencias do backend

Na raiz do projeto, rode:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_teste_windows.ps1
```

Se a maquina usa `python` em vez de `py`, rode:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_teste_windows.ps1 -PythonCommand python
```

O script faz:

1. verifica Python;
2. avisa se `ffmpeg` ou `ffprobe` nao estao no `PATH`;
3. cria `.venv`;
4. instala `backend/requirements.txt`;
5. roda os testes automatizados.

No final esperado, os testes devem passar.

## 5. Iniciar o backend

Ainda na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe .\backend\main.py
```

Deixe esse terminal aberto. O backend deve subir em:

```text
http://127.0.0.1:8000
```

Em outro PowerShell, confirme:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Resposta esperada:

```json
{
  "status": "ok"
}
```

## 6. Teste direto do backend sem Premiere

Use um arquivo `.mp4` curto primeiro, de preferencia entre 30 segundos e 3 minutos.

Exemplo:

```powershell
$body = @{
  pgm_path = "C:\Aulas\teste_pgm.mp4"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/process" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

Resultado esperado:

```json
{
  "status": "success",
  "xml_path": "C:\\Aulas\\teste_pgm_cortado.xml"
}
```

Confirme se o arquivo XML foi criado ao lado do PGM.

## 7. Teste multicamera direto no backend

Quando o teste com PGM funcionar, teste com fontes sincronizadas:

```powershell
$body = @{
  pgm_path = "C:\Aulas\teste_pgm.mp4"
  camera_1_path = "C:\Aulas\teste_camera_1.mp4"
  camera_2_path = "C:\Aulas\teste_camera_2.mp4"
  ppt_path = "C:\Aulas\teste_ppt.mp4"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/process" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

As fontes opcionais precisam ter FPS compativel com o PGM e duracao igual ou maior que a do PGM. Se alguma fonte terminar antes do PGM, o backend retorna erro.

## 8. Carregar o plugin no Premiere

1. Abra o Premiere Pro.
2. Abra ou crie um projeto.
3. Abra o UXP Developer Tool.
4. Adicione o plugin apontando para a pasta:

```text
premiere_plugin/
```

5. Carregue o plugin.
6. No Premiere, abra o painel **Assistente de Decupagem**.

## 9. Teste completo pelo painel

Com o backend ainda rodando:

1. Clique em **Processar Aula**.
2. Selecione o video PGM quando solicitado.
3. Selecione Camera 1, Camera 2 e PPT/Tela se existirem.
4. Para pular uma fonte opcional, cancele a janela daquela selecao.
5. Aguarde o status **Processando na IA local...**.
6. Quando o backend concluir, o plugin deve importar o XML no projeto ativo.

Resultado esperado:

- um arquivo `*_cortado.xml` aparece na mesma pasta do PGM;
- o Premiere importa esse XML no bin atual;
- a timeline importada contem as fontes em trilhas sincronizadas;
- trechos descartados aparecem desabilitados no XML/timeline.

## 10. Checklist de problemas comuns

### Backend nao sobe

- Confirme se o ambiente virtual foi criado.
- Rode novamente `.\setup_teste_windows.ps1`.
- Verifique se a porta `8000` nao esta ocupada.

### Erro de FFmpeg/ffprobe

- Rode `ffmpeg -version` e `ffprobe -version`.
- Se falhar, corrija o `PATH`.

### Erro ao transcrever

- Comece com um `.mp4` curto.
- Confirme se o video tem faixa de audio.
- Na primeira execucao, o `faster-whisper` pode baixar/carregar o modelo e demorar mais.

### XML nao importa no Premiere

- Primeiro confirme se o XML foi gerado no teste direto do backend.
- Tente importar o XML manualmente pelo Premiere.
- Se a importacao manual falhar, o problema provavelmente esta na estrutura XMEML ou no caminho das midias.
- Se a importacao manual funcionar, o problema provavelmente esta na chamada UXP de importacao.

### Plugin nao conecta ao backend

- Confirme que o backend esta aberto em `http://127.0.0.1:8000/health`.
- Verifique se `premiere_plugin/main.js` aponta para `http://127.0.0.1:8000/process`.
- Recarregue o plugin no UXP Developer Tool.

## 11. Ordem recomendada para validar

1. Rodar `setup_teste_windows.ps1`.
2. Rodar `pytest` e confirmar testes passando.
3. Subir o backend.
4. Testar `/health`.
5. Testar `/process` com um PGM curto.
6. Abrir o XML manualmente/importar no Premiere.
7. Carregar o plugin UXP.
8. Rodar o fluxo completo pelo painel.
9. Testar multicamera com fontes sincronizadas.
