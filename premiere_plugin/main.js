/**
 * Painel UXP: coleta as fontes multicâmera e dispara o backend local.
 */

const BACKEND_URL = "http://127.0.0.1:8000/process";
const LETTERING_URL = "http://127.0.0.1:8000/analyze-lettering";
let ultimaSolicitacaoDeProcessamento = null;

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
    const revisaoEditorial = Boolean(document.getElementById("editorialReview").checked);
    const modoEconomico = Boolean(document.getElementById("economicMode").checked);
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
        pgm_path: pgmPath, project_type: "videocast", editorial_review: revisaoEditorial,
        editorial_mode: modoEconomico ? "economic" : "full",
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
        editorial_mode: modoEconomico ? "economic" : "full",
        skip_unsynced_sources: true,
        camera_1_path: await selecionarFonte("a Câmera 1", false),
        camera_2_path: await selecionarFonte("a Câmera 2", false),
        ppt_path: await selecionarFonte("o PPT/Tela", false),
        audio_path: await selecionarFonte(
          "o áudio final (Cancelar para usar o áudio do PGM)", false, tiposAudio
        ),
      };
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
    if (botao) botao.disabled = false;
  }
}

async function processarUltimaNoModoEconomico() {
  if (!ultimaSolicitacaoDeProcessamento) return;
  const botao = document.getElementById("btnModoEconomico");
  if (botao) botao.disabled = true;
  try {
    const fontes = { ...ultimaSolicitacaoDeProcessamento, editorial_mode: "economic" };
    ultimaSolicitacaoDeProcessamento = fontes;
    definirStatus("Processando a mesma aula no modo economico de IA...");
    const resposta = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fontes),
    });
    if (!resposta.ok) {
      const detalhe = await resposta.text();
      let mensagem = detalhe;
      try {
        const erro = JSON.parse(detalhe);
        mensagem = typeof erro.detail === "string" ? erro.detail : (erro.detail.message || detalhe);
      } catch (_) {}
      definirStatus("Erro no modo economico (HTTP " + resposta.status + "): " + mensagem);
      return;
    }
    await concluirProcessamento(await resposta.json());
  } catch (erro) {
    definirStatus("Erro: " + (erro && erro.message ? erro.message : String(erro)));
  } finally {
    if (botao) botao.disabled = false;
  }
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
  definirStatus("XML gerado. Importando no Premiere Pro...");
  await importarXmlNoPremiere(dados.xml_path);
  const sincronizacao = dados.synchronization || [];
  const resumo = sincronizacao.map((fonte) =>
    fonte.source + ": inicio no PGM " + fonte.offset_seconds.toFixed(3) + "s"
  ).join("; ");
  const modeloCortes = (dados.ai_models && dados.ai_models.cuts) || "modelo configurado";
  const ignoradas = (dados.skipped_sources || []).map((fonte) => fonte.source).join(", ");
  definirStatus("Sucesso! Timeline importada. " + resumo +
    (ignoradas ? " | Fontes ignoradas por falta de sincronizacao: " + ignoradas : "") +
    " | IA dos cortes: " + modeloCortes);
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
  if (botaoProcessarTopo) botaoProcessarTopo.onclick = botao.onclick;
  const botaoLettering = document.getElementById("btnLettering");
  if (botaoLettering) {
    botaoLettering.onclick = () => {
      analisarLettering().catch((erro) => {
        console.error("[Decupagem] Falha no clique de Lettering:", erro);
      });
    };
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
