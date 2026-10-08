/**
 * Painel UXP: coleta as fontes multicâmera e dispara o backend local.
 */

const BACKEND_URL = "http://127.0.0.1:8000/process";
const LETTERING_URL = "http://127.0.0.1:8000/analyze-lettering";
const LEARNING_URL = "http://127.0.0.1:8000/learning/compare-xml";
let ultimaSolicitacaoDeProcessamento = null;
let corteEmAndamento = false;
let ultimoXmlGeradoPath = null;
let ultimoDiagnosticoPath = null;
let feedbackAberto = null;
let indiceExemplo = 0;
let bancoOcupado = false;
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
  } catch (erro) {
    console.error("[Decupagem] Falha inesperada ao processar a aula:", erro);
    definirStatus(
      "Erro: " + (erro && erro.message ? erro.message : String(erro))
    );
  } finally {
    definirCorteEmAndamento(false);
  }
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
    { id: "letterings", titulo: "2. Letterings", tutorial: "<strong>Como usar</strong><ol><li>Finalize ou revise os cortes primeiro.</li><li>Selecione a sequência ativa com a transcrição correta.</li><li>O painel cria marcadores com sugestões; você decide quais textos entram na edição.</li></ol>" },
    { id: "aprender", titulo: "3. Aprender", tutorial: "<strong>Como usar</strong><ol><li>Exporte o XML criado pelo painel e o XML final após sua revisão.</li><li>Registre o par e confirme o motivo de cada alteração.</li><li>Reserve algumas edições para avaliação; elas não servem como referência.</li></ol>" },
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
    " | Trechos com marcador REVISAR: " + (dados.review_count || 0) + resumoTempo);
}

/**
 * Cria uma sequencia auxiliar com marcadores de lettering para a versao que
 * o editor ja revisou. O arquivo deve ser uma exportacao da sequencia final.
 * @returns {Promise<void>}
 */
async function analisarLettering() {
  const botao = document.getElementById("btnLettering");
  if (botao && botao.disabled) return;
  if (botao) botao.disabled = true;
  try {
    const premiere = require("premierepro");
    const projeto = await premiere.Project.getActiveProject();
    const sequencia = projeto && await projeto.getActiveSequence();
    if (!sequencia) throw new Error("Abra e deixe ativa a sequência revisada no Premiere Pro.");
    const segmentos = await obterTranscricaoDaSequencia(sequencia, premiere);
    const modoEconomico = Boolean(document.getElementById("economicMode").checked);
    if (!segmentos.length) throw new Error("A sequência ativa não possui transcrição com timestamps.");
    definirStatus("Analisando pontos didáticos da sequência ativa... Aguarde.");
    const resposta = await fetch("http://127.0.0.1:8000/analyze-lettering-segments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments: segmentos, economic_mode: modoEconomico }),
    });
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
    await adicionarMarcadoresNaSequencia(projeto, sequencia, premiere, dados.suggestions || []);
    const modeloLetterings = (dados.ai_models && dados.ai_models.letterings) ||
      "modelo configurado";
    definirStatus(
      "Sucesso! " + (dados.suggestions || []).length +
      " marcador(es) de lettering importado(s). | IA dos letterings: " +
      modeloLetterings
    );
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

async function registrarAprendizado() {
  const botao = document.getElementById("btnAprender");
  if (botao && botao.disabled) return;
  if (botao) botao.disabled = true;
  try {
    const sugerido = await selecionarFonte("o XML original gerado pela IA para esta edição", true, ["xml"]);
    if (!sugerido) return;
    const revisado = await selecionarFonte("o XML exportado da sequência final", true, ["xml"]);
    if (!revisado) return;
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
    definirStatus(mensagem + (resumoAprendizado.duplicate ? " Este par já estava registrado; suas anotações foram preservadas." : "") +
      " Confirme os motivos das alterações abaixo. " + (resumoAprendizado.warnings || []).join(" "));
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
  for (const r of dados.records) {
    const option = document.createElement("option");
    option.value = r.id;
    const nome = (r.revised_xml || "edição").split(/[\\/]/).pop();
    option.textContent = nome + " | " + r.approved + "/" + r.examples + " confirmados" +
      (r.needs_reimport ? " | reimporte os XMLs" : "") +
      (r.related_versions ? " | outras versões do mesmo material" : "");
    select.appendChild(option);
  }
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
  mostrarExemplo();
}

function mostrarExemplo() {
  if (!feedbackAberto) return;
  const r = feedbackAberto;
  const exemplos = r.examples || [];
  const e = exemplos[indiceExemplo];
  document.getElementById("feedbackDetails").textContent = "Original: " + r.suggested_xml +
    " | Revisado: " + r.revised_xml +
    (r.needs_reimport ? " | Registro antigo: selecione novamente os dois XMLs para validar fontes e recuperar contexto." :
      !r.eligible ? " | Somente consulta: faltam diagnóstico, fontes validadas ou tipo de projeto." : "");
  document.getElementById("feedbackRole").value = r.dataset_role || "reference";
  document.getElementById("feedbackReason").value = e && e.reason || "";
  document.getElementById("feedbackNote").value = e && e.note || "";
  const texto = (itens) => (itens || []).map(s => s.text || "[pausa]").join(" ");
  document.getElementById("exampleText").textContent = e ?
    "Alteração " + (indiceExemplo + 1) + " de " + exemplos.length + " | " + (e.status || "pending") +
    "\nTempo no material original: " + e.start_seconds + "s – " + e.end_seconds + "s" +
    "\n" + (e.editor_action === "cut" ? "A IA manteve; você cortou." : "A IA cortou; você recuperou.") +
    "\n\nAntes: " + texto(e.context_before) + "\n\nTrecho alterado: " + texto(e.segments) +
    "\n\nDepois: " + texto(e.context_after) : "Nenhum exemplo com transcrição disponível.";
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
          "Sugestão da IA — " + (sugestao.reason || "conceito")
        ));
      }
    }, "Adicionar sugestões de lettering");
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
    btnBanco: () => carregarBanco(), btnAbrirFeedback: abrirFeedback,
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
  const botaoAprender = document.getElementById("btnAprender");
  if (botaoAprender) {
    botaoAprender.onclick = () => registrarAprendizado().catch((erro) => console.error("[Decupagem] Falha ao registrar feedback:", erro));
  }
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
  definirStatus("Painel pronto. Escolha uma ação para iniciar.");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", iniciarPainel);
} else {
  iniciarPainel();
}
