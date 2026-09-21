/**
 * Painel UXP: coleta as fontes multicâmera e dispara o backend local.
 */

const BACKEND_URL = "http://127.0.0.1:8000/process";

const { localFileSystem } = require("uxp").storage;
const premiere = require("premierepro");

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
 * Solicita uma fonte de vídeo ao usuário.
 * @param {string} nomeFonte
 * @param {boolean} obrigatoria
 * @returns {Promise<string|null>}
 */
async function selecionarFonte(nomeFonte, obrigatoria) {
  definirStatus(
    "Selecione " + nomeFonte + (obrigatoria ? "." : " (Cancelar para pular).")
  );
  console.log("[Decupagem] Solicitando fonte:", nomeFonte);

  const arquivo = await localFileSystem.getFileForOpening({
    types: ["mp4"],
    allowMultiple: false,
  });

  if (!arquivo) {
    console.log("[Decupagem] Fonte não selecionada:", nomeFonte);
    return null;
  }

  console.log("[Decupagem] Fonte selecionada:", nomeFonte, arquivo.nativePath);
  return arquivo.nativePath;
}

/**
 * Importa o XMEML no bin atualmente selecionado no projeto ativo.
 * @param {string} xmlPath
 * @returns {Promise<void>}
 */
async function importarXmlNoPremiere(xmlPath) {
  const projeto = await premiere.Project.getActiveProject();
  if (!projeto) {
    throw new Error("Nenhum projeto ativo no Premiere Pro.");
  }
  const binDestino = await projeto.getInsertionBin();
  const importado = await projeto.importFiles(
    [xmlPath],
    false,
    binDestino,
    false
  );
  if (!importado) {
    throw new Error("O Premiere Pro recusou a importação do XML.");
  }
}

/**
 * Coleta PGM obrigatório e fontes opcionais, depois aciona o FastAPI.
 * @returns {Promise<void>}
 */
async function processarAula() {
  try {
    const pgmPath = await selecionarFonte("o vídeo PGM", true);
    if (!pgmPath) {
      definirStatus("Processamento cancelado: o PGM é obrigatório.");
      return;
    }

    const camera1Path = await selecionarFonte("a Câmera 1", false);
    const camera2Path = await selecionarFonte("a Câmera 2", false);
    const pptPath = await selecionarFonte("o PPT/Tela", false);

    definirStatus("Processando na IA local...");

    let resposta;
    try {
      resposta = await fetch(BACKEND_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          pgm_path: pgmPath,
          camera_1_path: camera1Path,
          camera_2_path: camera2Path,
          ppt_path: pptPath,
        }),
      });
    } catch (erroRede) {
      console.error("[Decupagem] Backend inacessível:", erroRede);
      definirStatus(
        "Erro de rede: " + (erroRede && erroRede.message ? erroRede.message : String(erroRede))
      );
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
      definirStatus("XML gerado. Importando no Premiere Pro...");
      await importarXmlNoPremiere(dados.xml_path);
      definirStatus("Sucesso! Timeline multicâmera importada.");
      return;
    }

    definirStatus("Erro: resposta inesperada do backend.");
  } catch (erro) {
    console.error("[Decupagem] Falha inesperada ao processar a aula:", erro);
    definirStatus(
      "Erro: " + (erro && erro.message ? erro.message : String(erro))
    );
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
