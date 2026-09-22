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
 * Solicita uma fonte de mídia ao usuário.
 * @param {string} nomeFonte
 * @param {boolean} obrigatoria
 * @returns {Promise<string|null>}
 */
async function selecionarFonte(nomeFonte, obrigatoria, tipos = ["mp4"]) {
  definirStatus(
    "Selecione " + nomeFonte + (obrigatoria ? "." : " (Cancelar para pular).")
  );
  console.log("[Decupagem] Solicitando fonte:", nomeFonte);

  const arquivo = await localFileSystem.getFileForOpening({
    types: tipos,
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
  const botao = document.getElementById("btnProcessar");
  if (botao && botao.disabled) return;
  if (botao) botao.disabled = true;
  try {
    const pgmPath = await selecionarFonte("o vídeo PGM", true);
    if (!pgmPath) {
      definirStatus("Processamento cancelado: o PGM é obrigatório.");
      return;
    }
    const tipoProjeto = document.getElementById("projectType").value;
    const tiposAudio = ["mp4", "mov", "m4a", "mp3", "wav", "aac"];
    let fontes;
    if (tipoProjeto === "videocast") {
      const participantes = [];
      for (let indice = 1; indice <= 4; indice += 1) {
        const video = await selecionarFonte("a câmera do Participante " + indice, false);
        const audio = video
          ? await selecionarFonte("o áudio do Participante " + indice, false, tiposAudio)
          : null;
        participantes.push({ video, audio });
      }
      const cameraGeral = await selecionarFonte("a Câmera geral", false);
      const audioCameraGeral = cameraGeral
        ? await selecionarFonte("o áudio da Câmera geral", false, tiposAudio)
        : null;
      const audioMaster = await selecionarFonte(
        "o áudio master/final (Cancelar para usar o áudio do PGM)", false, tiposAudio
      );
      fontes = {
        pgm_path: pgmPath, project_type: "videocast",
        participante_1_path: participantes[0].video,
        participante_2_path: participantes[1].video,
        participante_3_path: participantes[2].video,
        participante_4_path: participantes[3].video,
        camera_geral_path: cameraGeral,
        audio_participante_1_path: participantes[0].audio,
        audio_participante_2_path: participantes[1].audio,
        audio_participante_3_path: participantes[2].audio,
        audio_participante_4_path: participantes[3].audio,
        audio_camera_geral_path: audioCameraGeral,
        audio_path: audioMaster,
      };
    } else {
      fontes = {
        pgm_path: pgmPath, project_type: "videoaula",
        camera_1_path: await selecionarFonte("a Câmera 1", false),
        camera_2_path: await selecionarFonte("a Câmera 2", false),
        ppt_path: await selecionarFonte("o PPT/Tela", false),
        audio_path: await selecionarFonte(
          "o áudio final (Cancelar para usar o áudio do PGM)", false, tiposAudio
        ),
      };
    }

    definirStatus("Sincronizando pelo audio e processando a aula... Aguarde.");

    let resposta;
    try {
      resposta = await fetch(BACKEND_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(fontes),
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
      let mensagem = detalhe;
      try {
        const erro = JSON.parse(detalhe);
        mensagem = typeof erro.detail === "string" ? erro.detail : detalhe;
      } catch (_) {
        // Servidores intermediarios podem retornar texto em vez de JSON.
      }
      definirStatus(
        "Erro ao processar a aula (HTTP " + resposta.status + "): " + mensagem
      );
      return;
    }

    const dados = await resposta.json();
    console.log("[Decupagem] Resposta do backend:", dados);

    if (dados.status === "success" && dados.xml_path) {
      definirStatus("XML gerado. Importando no Premiere Pro...");
      await importarXmlNoPremiere(dados.xml_path);
      const sincronizacao = dados.synchronization || [];
      const resumo = sincronizacao.map((fonte) =>
        fonte.source + ": inicio no PGM " + fonte.offset_seconds.toFixed(3) + "s"
      ).join("; ");
      definirStatus("Sucesso! Timeline importada. " + resumo);
      return;
    }

    definirStatus("Erro: resposta inesperada do backend.");
  } catch (erro) {
    console.error("[Decupagem] Falha inesperada ao processar a aula:", erro);
    definirStatus(
      "Erro: " + (erro && erro.message ? erro.message : String(erro))
    );
  } finally {
    if (botao) botao.disabled = false;
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
