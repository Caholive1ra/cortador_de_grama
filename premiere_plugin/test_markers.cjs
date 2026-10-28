// node --test premiere_plugin/test_markers.cjs
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

test("auditoria e cortes leem Marker[] da colecao nao iteravel do Premiere", async () => {
  const actions = [];
  const items = [];
  const collection = {
    getMarkers: () => items,
    createAddMarkerAction: (name) => ({ name }),
  };
  const project = {
    getActiveSequence: async () => ({}),
    lockedAccess: (callback) => callback(),
    executeTransaction: (callback) => callback({
      addAction(action) {
        actions.push(action);
        if (action.name) items.push({
          getName: () => action.name,
          createSetColorByIndexAction: (color) => ({ color, marker: action.name }),
        });
      },
    }),
  };
  const premiere = {
    Project: { getActiveProject: async () => project },
    Markers: { getMarkers: async () => collection },
    TickTime: { createWithSeconds: (seconds) => ({ seconds }) },
    Constants: { MarkerColor: { RED: 1, BLUE: 2, GREEN: 3, CYAN: 4 } },
  };
  const context = vm.createContext({
    console,
    document: { readyState: "loading", addEventListener() {} },
    require: (name) => name === "uxp" ? { storage: { localFileSystem: {} } } : premiere,
  });
  vm.runInContext(fs.readFileSync(__dirname + "/main.js", "utf8"), context);
  await context.adicionarMarcadoresDeAuditoria(project, {}, premiere, [
    { start: 1, end: 2, title: "JUMP CUT", category: "jump_cut", description: "Teste" },
    { start: 3, end: 4, title: "IMAGEM", category: "imagem", description: "Teste" },
  ]);
  assert.equal(actions.find(a => a.marker === "AUDITORIA: JUMP CUT").color, 1);
  assert.equal(actions.find(a => a.marker === "AUDITORIA: IMAGEM").color, 2);
  items.push({ getName: () => "CORTE: SILÊNCIO",
    createSetColorByIndexAction: (color) => ({ color, marker: "CORTE: SILÊNCIO" }) });
  await context.colorirTagsDeCorteNaSequenciaAtiva();
  assert.equal(actions.find(a => a.marker === "CORTE: SILÊNCIO").color, 4);
});
