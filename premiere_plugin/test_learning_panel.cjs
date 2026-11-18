const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const html = fs.readFileSync(__dirname + "/index.html", "utf8");
const source = fs.readFileSync(__dirname + "/main.js", "utf8");

test("aprendizado oferece feedback geral multiplo e opcional", () => {
  const expected = [
    "palavras_repetidas_nao_removidas", "silencios_mantidos_indevidamente",
    "repeticoes_excessivas_nao_identificadas", "cortes_excessivamente_pequenos",
    "erros_de_fala_mantidos", "conteudo_importante_removido",
    "pontos_de_corte_imprecisos", "cortes_desnecessarios", "pausas_naturais_removidas", "outros",
  ];
  for (const value of expected) assert.match(html, new RegExp('type="checkbox" value="' + value + '"'));
  assert.match(html, /id="generalFeedbackNote" maxlength="500"/);
  assert.match(source, /querySelectorAll\("#generalFeedback input:checked"\)/);
  assert.match(source, /general_feedback: geral/);
  assert.match(source, /general_note:/);
});

test("aprendizado separa videoaula, graduacao e MBA", () => {
  assert.match(html, /value="videoaula"/);
  assert.match(html, /value="videocast_graduacao"/);
  assert.match(html, /value="videocast_mba"/);
});
