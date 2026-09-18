/**
 * Painel UXP: seleciona o áudio bruto e dispara o backend local.
 */

const BACKEND_URL = "http://127.0.0.1:8080/process";

const { localFileSystem } = require("uxp").storage;

/**
 * Atualiza o texto de status visível no painel.
 * @param {string} mensagem
 * @returns {void}
 */
function definirStatus(mensagem) {
  const statusText = document.getElementById("statusText");
  if (statusText) {
    statusText.textContent = mensagem;
  }
}

/**
 * Abre o seletor nativo, envia o path ao FastAPI e atualiza o status.
 * @returns {Promise<void>}
 */
async function processarAula() {
  try {
    console.log("[Decupagem] Abrindo seletor de arquivo .wav.");
    const arquivo = await localFileSystem.getFileForOpening({
      types: ["wav"],
      allowMultiple: false,
    });

    if (!arquivo) {
      console.log("[Decupagem] Seleção de arquivo cancelada.");
      definirStatus("Aguardando...");
      return;
    }

    const nativePath = arquivo.nativePath;
    console.log("[Decupagem] Arquivo selecionado:", nativePath);
    definirStatus("Processando na IA local...");

    let resposta;
    try {
      resposta = await fetch(BACKEND_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ file_path: nativePath }),
      });
    } catch (erroRede) {
      console.error("[Decupagem] Backend inacessível:", erroRede);
      definirStatus("Erro: Inicie o backend Python na porta 8080!");
      return;
    }

    if (!resposta.ok) {
      const detalhe = await resposta.text();
      console.error("[Decupagem] HTTP", resposta.status, detalhe);
      definirStatus("Erro ao processar a aula (HTTP " + resposta.status + ").");
      return;
    }

    const dados = await resposta.json();
    console.log("[Decupagem] Resposta do backend:", dados);

    if (dados.status === "success" && dados.xml_path) {
      definirStatus("Sucesso! XML gerado em: " + dados.xml_path);
      return;
    }

    definirStatus("Erro: resposta inesperada do backend.");
  } catch (erro) {
    console.error("[Decupagem] Falha inesperada ao processar a aula:", erro);
    definirStatus("Erro: Inicie o backend Python na porta 8080!");
  }
}

/**
 * Liga o clique do botão Processar Aula.
 * @returns {void}
 */
function iniciarPainel() {
  const botao = document.getElementById("btnProcessar");
  if (!botao) {
    console.error("[Decupagem] Botão btnProcessar não encontrado.");
    return;
  }
  botao.addEventListener("click", () => {
    processarAula().catch((erro) => {
      console.error("[Decupagem] Falha no clique de Processar Aula:", erro);
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", iniciarPainel);
} else {
  iniciarPainel();
}
