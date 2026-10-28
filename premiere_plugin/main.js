/**
 * Painel UXP: coleta as fontes multicâmera e dispara o backend local.
 */

const JOBS_URL = "http://127.0.0.1:8000/jobs";
const LEARNING_URL = "http://127.0.0.1:8000/learning/compare-xml";
const NOME_TAREFA = { process: "Corte", opening: "Abertura", audit: "Auditoria", lettering: "Lettering" };
let ultimaSolicitacaoDeProcessamento = null;
let corteEmAndamento = false;
let ultimoXmlGeradoPath = null;
let ultimoDiagnosticoPath = null;
let feedbackAberto = null;
let indiceExemplo = 0;
let bancoOcupado = false;
let atualizacaoFilaEmAndamento = false;
let tarefaCancelavelId = null;
const BANK_URL = "http://127.0.0.1:8000/learning/records";

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
 * Solicita uma fonte de mídia ao usuário.
 * @param {string} nomeFonte
 * @param {boolean} obrigatoria
 * @returns {Promise<string|null>}
 */
async function selecionarFonte(nomeFonte, obrigatoria, tipos = ["mp4", "mov", "mxf", "mkv", "avi", "webm"]) {
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
  // Carregamento tardio: uma falha na API do host nao impede os botoes de
  // abrir o seletor de arquivos e mostrar mensagens ao editor.
  const premiere = require("premierepro");
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
async function processarAula(fontesSalvas = null) {
  if (corteEmAndamento) return;
  definirCorteEmAndamento(true);
  try {
    let fontes = fontesSalvas ? { ...fontesSalvas } : null;
    if (!fontes) {
    const pgmPath = await selecionarFonte("o vídeo PGM", true);
    if (!pgmPath) {
      definirStatus("Processamento cancelado: o PGM é obrigatório.");
      return;
    }
    const tipoProjeto = document.getElementById("projectType").value;
    const revisaoEditorial = Boolean(document.getElementById("editorialReview").checked);
    const modoEconomico = Boolean(document.getElementById("economicMode").checked);
    const tiposAudio = ["mp4", "mov", "m4a", "mp3", "wav", "aac"];
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
        pgm_path: pgmPath, project_type: "videocast", editorial_review: revisaoEditorial,
        audio_follows_camera: Boolean(document.getElementById("audioFollowsCamera").checked),
        editorial_mode: modoEconomico ? "economic" : "auto",
        skip_unsynced_sources: true,
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
        pgm_path: pgmPath, project_type: "videoaula", editorial_review: revisaoEditorial,
        editorial_mode: modoEconomico ? "economic" : "auto",
        skip_unsynced_sources: true,
        camera_1_path: await selecionarFonte("a Câmera 1", false),
        camera_2_path: await selecionarFonte("a Câmera 2", false),
        ppt_path: await selecionarFonte("o PPT/Tela", false),
        audio_path: await selecionarFonte(
          "o áudio final (Cancelar para usar o áudio do PGM)", false, tiposAudio
        ),
      };
    }

    }
    definirStatus("Sincronizando pelo audio e processando a aula... Aguarde.");

    ultimaSolicitacaoDeProcessamento = fontes;
    definirStatus("Corte enviado para a fila com prioridade alta.");
    const job = await enfileirarTarefa("process", fontes);
    const dadosDaFila = await aguardarTarefa(job, "Corte da aula");
    console.log("[Decupagem] Resposta da fila:", dadosDaFila);
    await concluirProcessamento(dadosDaFila);
    return;

    /* Implementacao direta mantida abaixo apenas como referencia temporaria.
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
        if (
          resposta.status === 409 && erro.detail &&
          erro.detail.code === "ECONOMIC_MODE_REQUIRED"
        ) {
          mostrarAcaoModoEconomico(true);
          definirStatus("Esta aula tem mais de uma hora. Clique em 'Processar esta aula no modo economico'.");
          return;
        }
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

    await concluirProcessamento(dados);
    */
  } catch (erro) {
    if (erro && erro.detail && erro.detail.code === "ECONOMIC_MODE_REQUIRED") {
      mostrarAcaoModoEconomico(true);
      definirStatus("Esta aula tem mais de uma hora. Clique em 'Processar esta aula no modo economico'.");
      return;
    }
    console.error("[Decupagem] Falha inesperada ao processar a aula:", erro);
    definirStatus(
      "Erro: " + (erro && erro.message ? erro.message : String(erro))
    );
  } finally {
    definirCorteEmAndamento(false);
  }
}

function resumoOperacao(dados) {
  const resumo = dados.operation_summary || {};
  const modelos = Object.entries(resumo.models || {})
    .filter(([, modelo]) => modelo && modelo !== "não usada")
    .map(([etapa, modelo]) => etapa + ": " + modelo).join(" | ");
  const quantidades = Object.entries(resumo.quantities || {})
    .map(([nome, valor]) => nome.replaceAll("_", " ") + ": " + valor).join(" | ");
  return [quantidades, modelos ? "Modelos — " + modelos : ""].filter(Boolean).join(" | ");
}

function mostrarResumoOperacao(titulo, mensagem, dados) {
  const popup = document.getElementById("operationPopup");
  if (!popup) return;
  document.getElementById("operationPopupTitle").textContent = titulo;
  document.getElementById("operationPopupMessage").textContent = mensagem;
  const resumo = dados.operation_summary || {};
  const quantidades = Object.entries(resumo.quantities || {})
    .map(([nome, valor]) => nome.replaceAll("_", " ") + ": " + valor).join("\n");
  const modelos = Object.entries(resumo.models || {})
    .filter(([, modelo]) => modelo)
    .map(([etapa, modelo]) => etapa + ": " + modelo).join("\n");
  document.getElementById("operationPopupSummary").textContent =
    "Quantidades\n" + (quantidades || "Não informado") + "\n\nModelos\n" + (modelos || "Não informado");
  popup.hidden = false;
}

function fecharResumoOperacao() {
  const popup = document.getElementById("operationPopup");
  if (popup) popup.hidden = true;
}

function atualizarBotaoCancelar() {
  const botao = document.getElementById("btnCancelarTarefa");
  if (!botao) return;
  botao.classList.toggle("visivel", Boolean(tarefaCancelavelId));
  botao.disabled = !tarefaCancelavelId;
}

function textoEstadoJob(job) {
  const nomes = { queued: "Aguardando", running: "Em execucao", succeeded: "Concluida",
    failed: "Falhou", interrupted: "Interrompida", cancelled: "Cancelada" };
  let texto = nomes[job.status] || job.status;
  if (job.status === "running") texto = "EXECUTANDO AGORA";
  if (job.status === "queued") texto = "AGUARDANDO" + (job.queue_position === 1 ? " | PROXIMA" : " | depois de " + (job.queue_position - 1) + " tarefa(s)");
  if (job.cancel_requested) texto += " | cancelamento solicitado";
  if (job.attempts) texto += " | tentativa " + job.attempts;
  if (job.error && job.error.detail) texto += " | " + job.error.detail;
  return texto;
}

async function acaoJob(id, acao) {
  const resposta = await fetch(JOBS_URL + "/" + encodeURIComponent(id) + "/" + acao, { method: "POST" });
  if (!resposta.ok) throw new Error(await resposta.text());
  await atualizarCentralDeTarefas();
}

async function atualizarCentralDeTarefas() {
  const lista = document.getElementById("jobList");
  if (!lista || atualizacaoFilaEmAndamento) return;
  atualizacaoFilaEmAndamento = true;
  try {
    const resposta = await fetch(JOBS_URL + "?active_only=true");
    if (!resposta.ok) throw new Error("HTTP " + resposta.status);
    const dados = await resposta.json();
    while (lista.firstChild) lista.removeChild(lista.firstChild);
    const jobs = (dados.jobs || []).slice(0, 6);
    if (!jobs.length) { lista.textContent = "Fila vazia: nenhuma tarefa em andamento ou aguardando."; return; }
    for (const job of jobs) {
      const item = document.createElement("div");
      item.className = "job-item " + job.status;
      const titulo = document.createElement("span"); titulo.className = "job-titulo";
      titulo.textContent = job.title || ((NOME_TAREFA[job.task] || job.task) + " sem titulo");
      const detalhe = document.createElement("span"); detalhe.className = "job-detalhe";
      detalhe.textContent = textoEstadoJob(job);
      item.appendChild(titulo); item.appendChild(detalhe);
      if (job.status === "queued" || job.status === "running" || ["failed", "interrupted", "cancelled"].includes(job.status)) {
        const acoes = document.createElement("div"); acoes.className = "job-acoes";
        if (job.status === "queued" || job.status === "running") {
          const cancelar = document.createElement("button");
          cancelar.textContent = job.status === "running" ? "Solicitar cancelamento" : "Cancelar";
          cancelar.disabled = Boolean(job.cancel_requested);
          cancelar.onclick = () => acaoJob(job.id, "cancel").catch((erro) => definirStatus("Fila: " + erro.message));
          acoes.append(cancelar);
        } else {
          const repetir = document.createElement("button"); repetir.textContent = "Repetir";
          repetir.onclick = () => acaoJob(job.id, "retry").catch((erro) => definirStatus("Fila: " + erro.message));
          acoes.append(repetir);
        }
        item.appendChild(acoes);
      }
      lista.appendChild(item);
    }
  } catch (erro) {
    lista.textContent = "Central indisponivel: " + (erro.message || String(erro));
  } finally {
    atualizacaoFilaEmAndamento = false;
  }
}

/** Envia uma tarefa pesada e recebe seu identificador persistente na fila. */
async function enfileirarTarefa(task, payload) {
  let resposta;
  try {
    resposta = await fetch(JOBS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, payload }),
    });
  } catch (erro) {
    throw new Error("Backend inacessível: " + (erro.message || String(erro)));
  }
  if (!resposta.ok) {
    const detalhe = await resposta.text();
    throw new Error("Não foi possível entrar na fila (HTTP " + resposta.status + "): " + detalhe);
  }
  const job = await resposta.json();
  atualizarCentralDeTarefas();
  return job;
}

/** Aguarda a tarefa sem travar o painel e mostra a posição enquanto ela espera. */
async function aguardarTarefa(job, nome) {
  tarefaCancelavelId = job.id;
  atualizarBotaoCancelar();
  try {
    while (true) {
      const resposta = await fetch(JOBS_URL + "/" + encodeURIComponent(job.id));
      if (!resposta.ok) throw new Error("Não foi possível consultar a tarefa (HTTP " + resposta.status + ").");
      const atual = await resposta.json();
      if (atual.status === "queued") {
      const emExecucao = atual.running_job && NOME_TAREFA[atual.running_job.task];
      definirStatus(
        nome + " aguardando" +
        (atual.queue_position ? " (posicao " + atual.queue_position + " entre as aguardando)" : "") +
        (emExecucao ? ". Em execucao agora: " + emExecucao + "." : ".")
      );
      await new Promise((resolve) => setTimeout(resolve, 1200));
      continue;
      } else if (atual.status === "running") {
      definirStatus(nome + " em processamento. As demais tarefas aguardam na fila.");
      await new Promise((resolve) => setTimeout(resolve, 1200));
      continue;
    } else if (atual.status === "succeeded") {
      return atual.result;
    } else if (atual.status === "failed") {
      const erro = atual.error || {};
      const falha = new Error(typeof erro.detail === "string" ? erro.detail : "A tarefa falhou (HTTP " + (erro.status_code || 500) + ").");
      falha.statusCode = erro.status_code;
      falha.detail = erro.detail;
      throw falha;
    } else if (atual.status === "cancelled") {
      const cancelada = new Error("Tarefa cancelada pelo operador.");
      cancelada.cancelled = true;
      throw cancelada;
    } else {
      throw new Error("Estado desconhecido da tarefa: " + atual.status);
    }
    }
  } finally {
    if (tarefaCancelavelId === job.id) {
      tarefaCancelavelId = null;
      atualizarBotaoCancelar();
    }
  }
}

/** Compatibiliza a resposta da fila com as rotinas ja existentes do painel. */
async function filaComoResposta(task, payload, nome) {
  const job = await enfileirarTarefa(task, payload);
  const dados = await aguardarTarefa(job, nome);
  return { ok: true, json: async () => dados };
}

/**
 * Exporta a sequencia ativa para um XML temporario controlado pelo painel.
 * A API foi introduzida no Premiere 26.2; quem usa uma versao anterior ainda
 * pode selecionar manualmente um XML final ja exportado.
 */
async function exportarSequenciaAtivaParaAprendizado() {
  const premiere = require("premierepro");
  const projeto = await premiere.Project.getActiveProject();
  if (!projeto) throw new Error("Nenhum projeto ativo no Premiere Pro.");
  const sequencia = await projeto.getActiveSequence();
  if (!sequencia) throw new Error("Deixe ativa a sequência final que será usada para aprendizado.");
  const conversor = premiere.ProjectConverter;
  if (!conversor || typeof conversor.exportAsFinalCutProXML !== "function") {
    throw new Error("EXPORTACAO_XML_INDISPONIVEL");
  }
  const pastaDados = await localFileSystem.getDataFolder();
  const nomeSeguro = String(sequencia.name || "sequencia")
    .replace(/[<>:"/\\|?*]+/g, "_").slice(0, 80) || "sequencia";
  const caminhoSaida = pastaDados.nativePath.replace(/[\\/]$/, "") + "\\" +
    "decupagem_revisado_" + nomeSeguro + "_" + Date.now() + ".xml";
  const exportado = await conversor.exportAsFinalCutProXML(
    sequencia, caminhoSaida, true
  );
  if (!exportado) throw new Error("O Premiere não conseguiu exportar a sequência ativa como FCP XML.");
  return caminhoSaida;
}

/** Bloqueia envios simultaneos e oferece repetir apenas depois de uma tentativa. */
function definirCorteEmAndamento(ocupado) {
  corteEmAndamento = ocupado;
  for (const id of ["btnProcessar", "btnProcessarTopo", "btnTentarNovamente", "btnModoEconomico",
    "projectType", "editorialReview", "economicMode", "audioFollowsCamera"]) {
    const elemento = document.getElementById(id);
    if (elemento) elemento.disabled = ocupado;
  }
  const repetir = document.getElementById("btnTentarNovamente");
  if (repetir) repetir.classList.toggle("visivel", !ocupado && Boolean(ultimaSolicitacaoDeProcessamento));
}

/** Repete as fontes e opcoes da ultima solicitacao, sem abrir seletores. */
async function tentarNovamente() {
  if (!ultimaSolicitacaoDeProcessamento || corteEmAndamento) return;
  const fontes = ultimaSolicitacaoDeProcessamento;
  document.getElementById("projectType").value = fontes.project_type;
  document.getElementById("editorialReview").checked = fontes.editorial_review;
  document.getElementById("economicMode").checked = fontes.editorial_mode === "economic";
  document.getElementById("audioFollowsCamera").checked = Boolean(fontes.audio_follows_camera);
  atualizarOpcoesVideocast();
  await processarAula(fontes);
}

function criarTutorial(texto) {
  const caixa = document.createElement("div");
  caixa.className = "tutorial";
  caixa.innerHTML = texto;
  return caixa;
}

function atualizarOpcoesVideocast() {
  const opcao = document.getElementById("audioFollowsCameraOption");
  if (opcao) opcao.classList.toggle("visivel", document.getElementById("projectType").value === "videocast");
}

function configurarAbas() {
  const secoes = Array.from(document.querySelectorAll("main.painel > section.bloco"));
  if (secoes.length < 3 || document.querySelector(".abas")) return;
  const definicoes = [
    { id: "cortar", titulo: "1. Cortar", tutorial: "<strong>Como usar</strong><ol><li>Escolha se é vídeo-aula ou videocast.</li><li>Ative a revisão por IA para avaliar erros, retakes e bastidores.</li><li>Selecione as fontes ao processar. O resultado abre em uma nova sequência no Premiere.</li></ol>" },
    { id: "abertura", titulo: "2. Abertura", tutorial: "<strong>Como usar</strong><ol><li>Com o videocast finalizado, escolha seu render completo.</li><li>A ferramenta encontra falas fortes em partes distintas da conversa.</li><li>Importe a sequência curta e ajuste as escolhas editoriais finais.</li></ol>" },
    { id: "letterings", titulo: "3. Letterings", tutorial: "<strong>Como usar</strong><ol><li>Finalize os cortes e deixe a sequência revisada ativa.</li><li>Informe o contexto do curso e selecione o render completo dessa sequência, desde o início.</li><li>Revise as sugestões de até quatro palavras e as siglas nos marcadores.</li></ol>" },
    { id: "aprender", titulo: "4. Aprender", tutorial: "<strong>Como usar</strong><ol><li>Deixe ativa a sequência final; o painel exporta o XML automaticamente.</li><li>Selecione o XML inicial criado pela IA e confirme os motivos de cada alteração.</li><li>Reserve algumas edições para avaliação; elas não servem como referência.</li></ol>" },
    { id: "auditar", titulo: "5. Auditar", tutorial: "<strong>Como usar</strong><ol><li>Finalize sua edição e deixe a sequência ativa.</li><li>Selecione o render/proxy final da sequência.</li><li>Revise os marcadores coloridos: vermelho para jump cut, e cores distintas para áudio, imagem, continuidade e conteúdo.</li></ol>" },
  ];
  const navegacao = document.createElement("nav");
  navegacao.className = "abas";
  navegacao.setAttribute("aria-label", "Áreas do painel");
  const mostrar = (id) => {
    for (const painel of document.querySelectorAll(".tab-panel")) painel.hidden = painel.dataset.tab !== id;
    for (const aba of navegacao.querySelectorAll(".aba")) {
      const ativa = aba.dataset.tab === id;
      aba.classList.toggle("ativa", ativa);
      aba.setAttribute("aria-selected", String(ativa));
    }
  };
  definicoes.forEach((definicao, indice) => {
    const painel = secoes[indice];
    painel.classList.add("tab-panel");
    painel.dataset.tab = definicao.id;
    painel.hidden = indice !== 0;
    painel.insertBefore(criarTutorial(definicao.tutorial), painel.children[2] || null);
    const aba = document.createElement("button");
    aba.type = "button";
    aba.className = "aba" + (indice === 0 ? " ativa" : "");
    aba.dataset.tab = definicao.id;
    aba.textContent = definicao.titulo;
    aba.setAttribute("aria-selected", String(indice === 0));
    aba.onclick = () => mostrar(definicao.id);
    navegacao.appendChild(aba);
  });
  const cabecalho = document.querySelector(".cabecalho");
  cabecalho.insertAdjacentElement("afterend", navegacao);
}

async function processarUltimaNoModoEconomico() {
  if (!ultimaSolicitacaoDeProcessamento || corteEmAndamento) return;
  document.getElementById("economicMode").checked = true;
  await processarAula({ ...ultimaSolicitacaoDeProcessamento, editorial_mode: "economic" });
}

function mostrarAcaoModoEconomico(mostrar) {
  const botao = document.getElementById("btnModoEconomico");
  if (botao) botao.classList.toggle("visivel", mostrar);
}

async function concluirProcessamento(dados) {
  if (dados.status !== "success" || !dados.xml_path) {
    definirStatus("Erro: resposta inesperada do backend.");
    return;
  }
  mostrarAcaoModoEconomico(false);
  ultimoXmlGeradoPath = dados.xml_path;
  ultimoDiagnosticoPath = dados.diagnostic_path || null;
  definirStatus("XML gerado. Importando no Premiere Pro...");
  await importarXmlNoPremiere(dados.xml_path);
  await colorirTagsDeCorteNaSequenciaAtiva();
  const sincronizacao = dados.synchronization || [];
  const resumo = sincronizacao.map((fonte) =>
    fonte.source + ": inicio no PGM " + fonte.offset_seconds.toFixed(3) + "s"
  ).join("; ");
  const modeloCortes = (dados.ai_models && dados.ai_models.cuts) || "modelo configurado";
  const ignoradas = (dados.skipped_sources || []).map((fonte) => fonte.source).join(", ");
  const tempos = dados.performance_seconds || {};
  const resumoTempo = tempos.total ? " | Tempo: " + Math.round(tempos.total) + "s" +
    (tempos.transcription ? " (transcrição " + Math.round(tempos.transcription - (tempos.audio_sync || 0)) + "s" : "") +
    (tempos.editorial_analysis ? ", IA " + Math.round(tempos.editorial_analysis - (tempos.transcription || 0)) + "s)" : ")") : "";
  definirStatus("Sucesso! Timeline importada. " + resumo +
    (ignoradas ? " | Fontes ignoradas por falta de sincronizacao: " + ignoradas : "") +
    " | IA dos cortes: " + modeloCortes +
    (dados.editorial_mode_used === "economic" ? " (modo econômico)" : "") +
    " | Trechos com marcador REVISAR: " + (dados.review_count || 0) + resumoTempo +
    (resumoOperacao(dados) ? " | " + resumoOperacao(dados) : ""));
  mostrarResumoOperacao("Corte concluído", "A timeline foi importada no Premiere.", dados);
}

/** Aplica uma cor por categoria às tags de corte da sequência recém-importada. */
async function colorirTagsDeCorteNaSequenciaAtiva() {
  const premiere = require("premierepro");
  const projeto = await premiere.Project.getActiveProject();
  const sequencia = projeto && await projeto.getActiveSequence();
  const cores = premiere.Constants && premiere.Constants.MarkerColor;
  if (!sequencia || !cores) return;

  const corParaTag = {
    "REPETIÇÃO": cores.RED,
    "TOSSE": cores.ORANGE,
    "RISADA": cores.YELLOW,
    "SILÊNCIO": cores.CYAN,
    "ERRO DE FALA": cores.RED,
    "FALA INADEQUADA": cores.MAGENTA || cores.MAGNETA,
    "CONVERSA LATERAL": cores.MAGENTA || cores.MAGNETA,
    "DESVIO DE ASSUNTO": cores.MAGENTA || cores.MAGNETA,
    "PROBLEMA TÉCNICO": cores.BLUE,
    "BASTIDOR": cores.MAGENTA || cores.MAGNETA,
    "COMANDO DE CORTE": cores.ORANGE,
    "CORTE": cores.GREEN,
  };
  const marcadores = await premiere.Markers.getMarkers(sequencia);
  const listaMarcadores = await marcadores.getMarkers();
  projeto.lockedAccess(() => {
    projeto.executeTransaction((transacao) => {
      for (const marcador of listaMarcadores) {
        const nome = marcador.getName();
        if (!nome || !nome.startsWith("CORTE: ")) continue;
        const tag = nome.slice("CORTE: ".length);
        transacao.addAction(marcador.createSetColorByIndexAction(
          corParaTag[tag] || cores.GREEN
        ));
      }
    }, "Colorir tags de corte");
  });
}

/**
 * Cria uma sequencia auxiliar com marcadores de lettering para a versao que
 * o editor ja revisou. O arquivo deve ser uma exportacao da sequencia final.
 * @returns {Promise<void>}
 */
async function analisarLettering() {
  const botao = document.getElementById("btnLettering");
  if (botao && botao.dataset.task !== "lettering") {
    throw new Error("O botão de lettering está configurado com uma tarefa inválida.");
  }
  if (botao && botao.disabled) return;
  if (botao) botao.disabled = true;
  try {
    const premiere = require("premierepro");
    const projeto = await premiere.Project.getActiveProject();
    const sequencia = projeto && await projeto.getActiveSequence();
    if (!sequencia) throw new Error("Abra e deixe ativa a sequência revisada no Premiere Pro.");
    const mediaPath = await selecionarFonte("o render da sequência revisada completa, desde o início", true);
    if (!mediaPath) return;
    const modoEconomico = Boolean(document.getElementById("economicMode").checked);
    const contexto = document.getElementById("letteringContext");
    definirStatus("Transcrevendo a edição final e analisando o contexto completo da aula...");
    const resposta = await filaComoResposta("lettering", {
      revised_media_path: mediaPath, economic_mode: modoEconomico,
      course_context: contexto ? contexto.value : ""
    }, "Análise de lettering"); /*
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revised_media_path: mediaPath, economic_mode: modoEconomico,
        course_context: contexto ? contexto.value : "" }),
    }); */
    if (!resposta.ok) {
      const detalhe = await resposta.text();
      let mensagem = detalhe;
      try {
        const erro = JSON.parse(detalhe);
        mensagem = typeof erro.detail === "string" ? erro.detail : detalhe;
      } catch (_) {}
      definirStatus("Erro ao analisar lettering (HTTP " + resposta.status + "): " + mensagem);
      return;
    }
    const dados = await resposta.json();
    if (dados.status !== "success") {
      definirStatus("Erro: resposta inesperada da análise de lettering.");
      return;
    }
    const destino = await obterSequenciaDeDestino(projeto, sequencia.guid, sequencia.name);
    await adicionarMarcadoresNaSequencia(projeto, destino, premiere, dados.suggestions || []);
    const modeloLetterings = (dados.ai_models && dados.ai_models.letterings) ||
      "modelo configurado";
    definirStatus("Sucesso! " + (dados.suggestions || []).length +
      " marcador(es) de lettering inserido(s) em '" + destino.name + "'. | IA dos letterings: " + modeloLetterings +
      (resumoOperacao(dados) ? " | " + resumoOperacao(dados) : ""));
    mostrarResumoOperacao("Letterings concluídos", "Os marcadores foram inseridos em '" + destino.name + "'.", dados);
  } catch (erro) {
    console.error("[Decupagem] Falha na análise de lettering:", erro);
    const detalhe = erro && erro.message ? erro.message : String(erro);
    definirStatus(
      detalhe.toLowerCase().includes("invalid parameter")
        ? "A sequência ativa não possui uma transcrição de clipe compatível. Transcreva o áudio no Premiere e tente novamente."
        : "Erro: " + detalhe
    );
  } finally {
    if (botao) botao.disabled = false;
  }
}

async function criarAberturaVideocast() {
  const botao = document.getElementById("btnAbertura");
  if (botao && botao.disabled) return;
  if (botao) botao.disabled = true;
  try {
    const mediaPath = await selecionarFonte("o render completo do videocast", true);
    if (!mediaPath) return;
    definirStatus("Transcrevendo e selecionando falas de impacto para a abertura...");
    const resposta = await filaComoResposta("opening", { revised_media_path: mediaPath }, "Abertura de videocast");
    if (!resposta.ok) throw new Error(await resposta.text());
    const dados = await resposta.json();
    if (dados.status !== "success" || !dados.xml_path) throw new Error("A abertura não foi gerada.");
    await importarXmlNoPremiere(dados.xml_path);
    const modelo = (dados.ai_models && dados.ai_models.opening) || "modelo configurado";
    definirStatus("Abertura criada com " + (dados.highlights || []).length + " falas | Curadoria: " + modelo +
      (resumoOperacao(dados) ? " | " + resumoOperacao(dados) : "") + ". Revise o ritmo no Premiere.");
    mostrarResumoOperacao("Abertura concluída", "A sequência de abertura foi importada no Premiere.", dados);
  } catch (erro) {
    console.error("[Decupagem] Falha na abertura de videocast:", erro);
    definirStatus("Erro ao criar abertura: " + (erro.message || String(erro)));
  } finally {
    if (botao) botao.disabled = false;
  }
}

async function registrarAprendizado() {
  const botao = document.getElementById("btnAprender");
  if (botao && botao.disabled) return;
  if (botao) botao.disabled = true;
  try {
    let revisado;
    try {
      definirStatus("Exportando automaticamente a sequência final ativa...");
      revisado = await exportarSequenciaAtivaParaAprendizado();
    } catch (erroExportacao) {
      if (String(erroExportacao.message || erroExportacao) !== "EXPORTACAO_XML_INDISPONIVEL") {
        console.warn("[Decupagem] Exportação automática do XML final falhou:", erroExportacao);
      }
      revisado = await selecionarFonte(
        "o XML exportado da sequência final", true, ["xml"]
      );
      if (!revisado) return;
    }
    const sugerido = await selecionarFonte("o XML original gerado pela IA para esta edição", true, ["xml"]);
    if (!sugerido) return;
    definirStatus("Comparando a sugestão com sua edição final...");
    const resposta = await fetch(LEARNING_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        suggested_xml_path: sugerido, revised_xml_path: revisado,
        project_type: document.getElementById("projectType").value,
        diagnostic_path: sugerido === ultimoXmlGeradoPath ? ultimoDiagnosticoPath : null,
      }),
    });
    const dados = await resposta.json();
    if (!resposta.ok) throw new Error(dados.detail || "Não foi possível comparar os XMLs.");
    const metricas = dados.comparison.metrics;
    const resumoAprendizado = dados.learning_summary || {};
    const mensagem = (
      "Feedback salvo. Concordância de material mantido: " +
      (metricas.agreement_rate * 100).toFixed(1) + "% | " +
      "a IA manteve " + metricas.ai_kept_editor_cut_frames +
      " frames que você cortou; recuperou " + metricas.ai_cut_editor_kept_frames + " frames." +
      (resumoAprendizado.diagnostic_found
        ? " " + resumoAprendizado.examples_saved + " exemplo(s) com contexto salvo(s)."
        : " Diagnóstico original não encontrado; foram salvas apenas as métricas.")
    );
    await carregarBanco(resumoAprendizado.feedback_id);
    await abrirFeedback();
    const resultado = document.getElementById("learningResult");
    resultado.hidden = false;
    resultado.textContent = mensagem + (resumoAprendizado.duplicate
      ? " Esta edição já estava registrada; suas confirmações foram preservadas."
      : " Agora confirme, se quiser, as mudanças encontradas abaixo.");
    definirStatus("Comparação pronta. " + resumoOperacao(dados) + ". Revise as mudanças encontradas ou siga para a próxima edição.");
    mostrarResumoOperacao("Aprendizado concluído", "A comparação entre as duas sequências foi salva.", dados);
  } catch (erro) {
    definirStatus("Erro ao registrar feedback: " + (erro.message || String(erro)));
  } finally {
    if (botao) botao.disabled = false;
  }
}

async function bancoRequest(url, body) {
  const resposta = await fetch(url, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const dados = await resposta.json();
  if (!resposta.ok) throw new Error(dados.detail || "Falha ao consultar feedback.");
  return dados;
}

async function carregarBanco(selectedId) {
  const dados = await bancoRequest(BANK_URL);
  const select = document.getElementById("feedbackRecord");
  while (select.firstChild) select.removeChild(select.firstChild);
  const escolha = document.createElement("option");
  escolha.value = "";
  escolha.textContent = "Escolha uma edição";
  select.appendChild(escolha);
  dados.records.forEach((r, indice) => {
    const option = document.createElement("option");
    option.value = r.id;
    option.textContent = "Edição " + (indice + 1) + " — " + r.approved +
      " de " + r.examples + " mudanças confirmadas" +
      (r.needs_reimport ? " (precisa importar novamente)" : "");
    select.appendChild(option);
  });
  if (selectedId) select.value = selectedId;
  document.getElementById("bankSummary").textContent = dados.records.length + " registros reais. " +
    dados.excluded_test_records + " registro(s) de teste ignorado(s). " + dados.invalid_records + " registro(s) inválido(s).";
}

async function abrirFeedback() {
  const id = document.getElementById("feedbackRecord").value;
  if (!id) throw new Error("Registre um par de XMLs primeiro.");
  feedbackAberto = await bancoRequest(BANK_URL + "/" + id);
  indiceExemplo = 0;
  document.getElementById("evaluationResult").textContent = "";
  document.getElementById("learningReview").hidden = false;
  document.getElementById("feedbackWorkspace").hidden = false;
  mostrarExemplo();
}

async function alternarRevisaoAprendizado() {
  const area = document.getElementById("learningReview");
  area.hidden = !area.hidden;
  if (!area.hidden) await carregarBanco();
}

function mostrarExemplo() {
  if (!feedbackAberto) return;
  const r = feedbackAberto;
  const exemplos = r.examples || [];
  const e = exemplos[indiceExemplo];
  document.getElementById("feedbackDetails").textContent = r.needs_reimport
    ? "Esta edição é antiga e precisa ser importada novamente antes de ensinar."
    : !r.eligible ? "Esta edição pode ser consultada, mas ainda não está pronta para ensinar."
    : "Esta edição está pronta para ensinar com as suas escolhas.";
  document.getElementById("feedbackRole").value = r.dataset_role || "reference";
  document.getElementById("feedbackReason").value = e && e.reason || "";
  document.getElementById("feedbackNote").value = e && e.note || "";
  const texto = (itens) => (itens || []).map(s => s.text || "[pausa]").join(" ");
  document.getElementById("exampleText").textContent = e ?
    "Mudança " + (indiceExemplo + 1) + " de " + exemplos.length +
    "\n\nA IA " + (e.editor_action === "cut" ? "manteve este trecho, mas você cortou:" : "cortou este trecho, mas você manteve:") +
    "\n“" + texto(e.segments) + "”" +
    "\n\nContexto antes: " + texto(e.context_before) +
    "\n\nContexto depois: " + texto(e.context_after)
    : "Não há mudanças com transcrição disponível nesta edição.";
  document.getElementById("btnExamplePrev").disabled = indiceExemplo === 0;
  document.getElementById("btnExampleNext").disabled = indiceExemplo >= exemplos.length - 1;
  document.getElementById("btnConfirmExample").disabled = !e || !r.eligible || r.needs_reimport;
  document.getElementById("btnExcludeExample").disabled = !e || r.needs_reimport;
  document.getElementById("btnPendingExample").disabled = !e || r.needs_reimport;
}

async function anotarExemplo(status) {
  if (!feedbackAberto) throw new Error("Abra uma edição primeiro.");
  const e = feedbackAberto.examples[indiceExemplo];
  if (!e) throw new Error("Não há alteração selecionada.");
  const reason = document.getElementById("feedbackReason").value || null;
  feedbackAberto = await bancoRequest(BANK_URL + "/" + feedbackAberto.id + "/annotations", {
    example_ids: [e.id], status, reason, note: document.getElementById("feedbackNote").value,
  });
  mostrarExemplo();
  definirStatus("Revisão salva. Ela ainda não altera cortes automaticamente.");
}

async function salvarPapelFeedback() {
  if (!feedbackAberto) throw new Error("Abra uma edição primeiro.");
  feedbackAberto = await bancoRequest(BANK_URL + "/" + feedbackAberto.id + "/role", {
    role: document.getElementById("feedbackRole").value,
  });
  mostrarExemplo();
  definirStatus("Uso da edição salvo. Projetos de avaliação ficam separados das referências.");
}

async function avaliarNovoXml() {
  if (!feedbackAberto) throw new Error("Abra uma edição reservada para avaliação.");
  const id = feedbackAberto.id;
  const caminho = await selecionarFonte("o NOVO XML gerado para o mesmo material", true, ["xml"]);
  if (!caminho) return;
  const r = await bancoRequest(BANK_URL + "/" + id + "/evaluate", { candidate_xml: caminho });
  document.getElementById("evaluationResult").textContent =
    "Conteúdo que você preservou, mas a IA removeu: " + r.baseline.useful_removed_seconds + "s → " + r.candidate.useful_removed_seconds + "s." +
    "\nMaterial que você cortou, mas a IA manteve: " + r.baseline.unwanted_kept_seconds + "s → " + r.candidate.unwanted_kept_seconds + "s." +
    "\n" + r.interpretation + (r.useful_content_regressed ? " ATENÇÃO: aumentou a remoção de conteúdo preservado por você." : "");
  definirStatus("Comparação concluída. Nenhuma sequência foi modificada.");
}

async function executarAcaoBanco(acao) {
  if (bancoOcupado) return;
  bancoOcupado = true;
  try { await acao(); } catch (erro) { definirStatus("Feedback: " + (erro.message || String(erro))); }
  finally { bancoOcupado = false; }
}

async function obterTranscricaoDaSequencia(sequencia, premiere) {
  // O Premiere pode rejeitar a sequence project item como origem de transcript.
  // Nesse caso, lemos o primeiro clipe de video e deslocamos seus tempos para
  // a posicao dele na timeline.
  try {
    const itemSequencia = await sequencia.getProjectItem();
    const itemClipe = premiere.ClipProjectItem.cast(itemSequencia);
    const json = await exportarOuCriarTranscricao(premiere, itemClipe);
    return normalizarSegmentosTranscricao(JSON.parse(json));
  } catch (_) {
    const trilha = await sequencia.getVideoTrack(0);
    const tipoClipe = premiere.Constants && premiere.Constants.TrackItemType
      ? premiere.Constants.TrackItemType.CLIP : 1;
    const clipes = trilha && trilha.getTrackItems(tipoClipe, false);
    if (!clipes || !clipes.length) return [];
    const clipe = clipes[0];
    const item = premiere.ClipProjectItem.cast(await clipe.getProjectItem());
    const inicio = await clipe.getStartTime();
    const json = await exportarOuCriarTranscricao(premiere, item);
    return normalizarSegmentosTranscricao(JSON.parse(json)).map((segmento) => ({
      ...segmento,
      start: segmento.start + Number(inicio.seconds || 0),
      end: segmento.end + Number(inicio.seconds || 0),
    }));
  }
}

async function exportarOuCriarTranscricao(premiere, itemClipe) {
  try {
    return await premiere.Transcript.exportToJSON(itemClipe);
  } catch (_) {
    const criado = await premiere.Transcript.transcribeClipProjectItem(
      itemClipe, { language: "pt-BR" }
    );
    if (!criado) throw new Error("Não foi possível transcrever o clipe ativo no Premiere.");
    return await premiere.Transcript.exportToJSON(itemClipe);
  }
}

function normalizarSegmentosTranscricao(valor) {
  const lista = Array.isArray(valor) ? valor : (valor.segments || valor.textSegments || []);
  return lista.map((item) => {
    const inicio = Number(item.start ?? item.startTime ?? item.inicio ?? 0);
    const duracao = Number(item.duration ?? item.duracao ?? 0);
    const palavras = Array.isArray(item.words) ? item.words
      .map((word) => String(word.text || "").trim()).filter(Boolean).join(" ") : "";
    return {
      start: inicio,
      end: Number(item.end ?? item.endTime ?? item.fim ?? (inicio + duracao)),
      text: String(item.text ?? item.transcript ?? item.texto ?? palavras).trim(),
    };
  }).filter((item) => item.end > item.start && item.text);
}

async function adicionarMarcadoresNaSequencia(projeto, sequencia, premiere, sugestoes) {
  const marcadores = await premiere.Markers.getMarkers(sequencia);
  projeto.lockedAccess(() => {
    projeto.executeTransaction((transacao) => {
      for (const sugestao of sugestoes) {
        const inicio = premiere.TickTime.createWithSeconds(Number(sugestao.start));
        const duracao = premiere.TickTime.createWithSeconds(
          Math.max(0.1, Number(sugestao.end) - Number(sugestao.start))
        );
        transacao.addAction(marcadores.createAddMarkerAction(
          "LETTERING: " + sugestao.text, "Comment", inicio, duracao,
          "Categoria: " + (sugestao.reason || "conceito") +
          "\nPor que: " + (sugestao.explanation || "") +
          "\nEvidência: " + (sugestao.evidence || "")
        ));
      }
    }, "Adicionar sugestões de lettering");
  });
}

/** Reencontra o destino original, mesmo que o editor tenha mudado de aba. */
async function obterSequenciaDeDestino(projeto, guid, nome) {
  const destino = await projeto.getSequence(guid);
  if (!destino) {
    throw new Error(
      "A sequência de destino '" + nome + "' não está mais disponível. Nenhum marcador foi inserido."
    );
  }
  return destino;
}

async function auditarSequenciaFinal() {
  const botao = document.getElementById("btnAuditar");
  if (botao && botao.dataset.task !== "audit") {
    throw new Error("O botão de auditoria está configurado com uma tarefa inválida.");
  }
  if (botao && botao.disabled) return;
  if (botao) botao.disabled = true;
  try {
    const premiere = require("premierepro");
    const projeto = await premiere.Project.getActiveProject();
    const sequencia = projeto && await projeto.getActiveSequence();
    if (!sequencia) throw new Error("Deixe ativa a sequência final que será auditada.");
    definirStatus("Exportando a estrutura da sequência para auditoria...");
    let xmlPath;
    try {
      xmlPath = await exportarSequenciaAtivaParaAprendizado();
    } catch (erro) {
      if (String(erro.message || erro) !== "EXPORTACAO_XML_INDISPONIVEL") throw erro;
      xmlPath = await selecionarFonte("o XML exportado da sequência final", true, ["xml"]);
      if (!xmlPath) return;
    }
    const mediaPath = await selecionarFonte(
      "o render ou proxy final da sequência", true,
      ["mp4", "mov", "mxf", "mkv", "avi", "webm"]
    );
    if (!mediaPath) return;
    definirStatus("Auditando áudio, imagem, cortes e continuidade... Aguarde.");
    const resposta = await filaComoResposta("audit", {
      revised_media_path: mediaPath, revised_xml_path: xmlPath,
    }, "Auditoria da edição final"); /*
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revised_media_path: mediaPath, revised_xml_path: xmlPath }),
    }); */
    if (!resposta.ok) {
      const detalhe = await resposta.text();
      let mensagem = detalhe;
      try { const erro = JSON.parse(detalhe); mensagem = erro.detail || detalhe; } catch (_) {}
      throw new Error(typeof mensagem === "string" ? mensagem : "Não foi possível auditar a sequência.");
    }
    const dados = await resposta.json();
    const destino = await obterSequenciaDeDestino(projeto, sequencia.guid, sequencia.name);
    await adicionarMarcadoresDeAuditoria(projeto, destino, premiere, dados.findings || []);
    const resumo = Object.entries(dados.summary || {})
      .map(([categoria, total]) => total + " " + categoria).join(", ");
    const resumoOperacaoFinal = resumoOperacao(dados);
    const mensagemAuditoria = (dados.findings || []).length
      ? "Auditoria concluída: " + resumo + ". Marcadores inseridos em '" + destino.name + "'."
      : "Auditoria concluída: nenhum alerta automático encontrado. Faça ainda a revisão humana final.";
    definirStatus(mensagemAuditoria + (resumoOperacaoFinal ? " | " + resumoOperacaoFinal : ""));
    mostrarResumoOperacao("Auditoria concluída", mensagemAuditoria, dados);
  } catch (erro) {
    console.error("[Decupagem] Falha na auditoria:", erro);
    definirStatus("Auditoria: " + (erro.message || String(erro)));
  } finally {
    if (botao) botao.disabled = false;
  }
}

async function adicionarMarcadoresDeAuditoria(projeto, sequencia, premiere, findings) {
  if (!findings.length) return;
  const marcadores = await premiere.Markers.getMarkers(sequencia);
  projeto.lockedAccess(() => {
    projeto.executeTransaction((transacao) => {
      for (const item of findings) {
        const inicio = premiere.TickTime.createWithSeconds(Number(item.start));
        const duracao = premiere.TickTime.createWithSeconds(Math.max(.1, Number(item.end) - Number(item.start)));
        transacao.addAction(marcadores.createAddMarkerAction(
          "AUDITORIA: " + item.title, "Comment", inicio, duracao, item.description
        ));
      }
    }, "Adicionar marcadores da auditoria");
  });
  const cores = (premiere.Constants && premiere.Constants.MarkerColor) || {};
  const porCategoria = {
    jump_cut: cores.RED, continuidade: cores.ORANGE, repeticao: cores.YELLOW,
    audio: cores.CYAN, imagem: cores.BLUE, conteudo: cores.MAGENTA || cores.MAGNETA,
    revisao: cores.GREEN,
  };
  const colecaoCriados = await premiere.Markers.getMarkers(sequencia);
  const criados = await colecaoCriados.getMarkers();
  projeto.lockedAccess(() => {
    projeto.executeTransaction((transacao) => {
      for (const marcador of criados) {
        const nome = marcador.getName();
        if (!nome || !nome.startsWith("AUDITORIA: ")) continue;
        const categoria = findings.find((item) => "AUDITORIA: " + item.title === nome)?.category;
        if (categoria && porCategoria[categoria] !== undefined) {
          transacao.addAction(marcador.createSetColorByIndexAction(porCategoria[categoria]));
        }
      }
    }, "Colorir marcadores da auditoria");
  });
}

/**
 * Liga o clique do botão Processar Aula.
 * @returns {void}
 */
function iniciarPainel() {
  configurarAbas();
  document.getElementById("projectType").onchange = atualizarOpcoesVideocast;
  atualizarOpcoesVideocast();
  const acoesBanco = {
    btnBanco: alternarRevisaoAprendizado, btnAbrirFeedback: abrirFeedback,
    btnExamplePrev: () => { if (feedbackAberto && indiceExemplo > 0) { indiceExemplo--; mostrarExemplo(); } },
    btnExampleNext: () => { if (feedbackAberto && indiceExemplo < feedbackAberto.examples.length - 1) { indiceExemplo++; mostrarExemplo(); } },
    btnConfirmExample: () => anotarExemplo("approved"), btnExcludeExample: () => anotarExemplo("excluded"),
    btnPendingExample: () => anotarExemplo("pending"), btnSaveRole: salvarPapelFeedback, btnEvaluate: avaliarNovoXml,
  };
  for (const id of Object.keys(acoesBanco)) {
    const elemento = document.getElementById(id);
    if (elemento) elemento.onclick = () => executarAcaoBanco(acoesBanco[id]);
  }
  const botao = document.getElementById("btnProcessar");
  if (!botao) {
    console.error("[Decupagem] Botão btnProcessar não encontrado.");
    return;
  }
  botao.onclick = () => {
    processarAula().catch((erro) => {
      console.error("[Decupagem] Falha no clique de Processar Aula:", erro);
    });
  };
  const botaoProcessarTopo = document.getElementById("btnProcessarTopo");
  const botaoRepetir = document.getElementById("btnTentarNovamente");
  if (botaoRepetir) botaoRepetir.onclick = tentarNovamente;
  if (botaoProcessarTopo) botaoProcessarTopo.onclick = botao.onclick;
  const botaoLettering = document.getElementById("btnLettering");
  if (botaoLettering) {
    botaoLettering.onclick = () => {
      analisarLettering().catch((erro) => {
        console.error("[Decupagem] Falha no clique de Lettering:", erro);
      });
    };
  }
  const botaoAbertura = document.getElementById("btnAbertura");
  if (botaoAbertura) botaoAbertura.onclick = () => criarAberturaVideocast();
  const botaoAprender = document.getElementById("btnAprender");
  if (botaoAprender) {
    botaoAprender.onclick = () => registrarAprendizado().catch((erro) => console.error("[Decupagem] Falha ao registrar feedback:", erro));
  }
  const botaoAuditar = document.getElementById("btnAuditar");
  if (botaoAuditar) botaoAuditar.onclick = () => auditarSequenciaFinal();
  const botaoLetteringTopo = document.getElementById("btnLetteringTopo");
  if (botaoLetteringTopo && botaoLettering) {
    botaoLetteringTopo.onclick = botaoLettering.onclick;
  }
  const botaoModoEconomico = document.getElementById("btnModoEconomico");
  if (botaoModoEconomico) {
    botaoModoEconomico.onclick = () => {
      processarUltimaNoModoEconomico().catch((erro) => {
        console.error("[Decupagem] Falha no modo economico:", erro);
      });
    };
  }
  const botaoCancelarTarefa = document.getElementById("btnCancelarTarefa");
  if (botaoCancelarTarefa) botaoCancelarTarefa.onclick = async () => {
    if (!tarefaCancelavelId) return;
    botaoCancelarTarefa.disabled = true;
    try {
      await acaoJob(tarefaCancelavelId, "cancel");
      definirStatus("Cancelamento solicitado. A tarefa será interrompida ao concluir a etapa segura atual.");
    } catch (erro) {
      definirStatus("Não foi possível cancelar: " + (erro.message || String(erro)));
      atualizarBotaoCancelar();
    }
  };
  atualizarBotaoCancelar();
  const botaoFecharResumo = document.getElementById("btnFecharResumo");
  if (botaoFecharResumo) botaoFecharResumo.onclick = fecharResumoOperacao;
  const popupResumo = document.getElementById("operationPopup");
  if (popupResumo) popupResumo.onclick = (evento) => {
    if (evento.target === popupResumo) fecharResumoOperacao();
  };
  definirStatus("Painel pronto. Escolha uma ação para iniciar.");
  atualizarCentralDeTarefas();
  setInterval(atualizarCentralDeTarefas, 2000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", iniciarPainel);
} else {
  iniciarPainel();
}
