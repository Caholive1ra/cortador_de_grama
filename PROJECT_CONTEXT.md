# CONTEXTO DO PROJETO: ASSISTENTE DE DECAPAGEM (PANCAKE EDITING)

## O Problema
Recebemos aulas brutas (PGM multicâmera) de até 4 horas. O editor gasta muito tempo assistindo ao vídeo inteiro apenas para encontrar pausas, erros e retakes do professor. 

## A Solução (O Produto)
Uma ferramenta híbrida (Plugin UXP + Backend Local) que "ouve" o vídeo em alta velocidade e gera uma timeline de pré-edição baseada no método "Pancake Editing". 
A máquina NUNCA deleta mídia. Ela reorganiza os trechos em duas trilhas de vídeo:
- **Trilha V2 (Elevada):** Trechos considerados bons (fala contínua e limpa).
- **Trilha V1 (Rebaixada):** Trechos marcados como erros (palavras-gatilho como "errei", "desculpa", "corta") e grandes lacunas de silêncio.

## Fluxo de Dados (Data Flow)
1. **Acionamento:** O usuário clica em "Analisar Aula" no painel nativo dentro do Premiere Pro.
2. **Coleta de Path:** O JS do Plugin identifica o caminho absoluto (`C:\...\audio.wav`) do arquivo na máquina do usuário.
3. **Requisição HTTP:** O Plugin faz um POST para `http://localhost:8000/process` enviando o path.
4. **Transcrição (Backend):** O FastAPI recebe o path, carrega o arquivo no `faster-whisper` e gera o texto com `timestamps` exatos.
5. **Motor de Regras:** O Python analisa o texto buscando padrões de erro e classifica cada timestamp como `V1` ou `V2`.
6. **Geração do XML:** O Python constrói um arquivo FCP XML (Final Cut Pro XML, nativamente aceito pelo Premiere) com os cortes.
7. **Retorno e Importação:** A API devolve o path do arquivo `.xml` gerado. O Plugin recebe o path e usa as funções do Adobe UXP para importar esse XML para dentro do projeto ativo.