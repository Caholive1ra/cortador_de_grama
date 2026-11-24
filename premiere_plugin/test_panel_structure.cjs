// node --test premiere_plugin/test_panel_structure.cjs
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync(__dirname + "/index.html", "utf8");
const source = fs.readFileSync(__dirname + "/main.js", "utf8");
const manifest = JSON.parse(fs.readFileSync(__dirname + "/manifest.json", "utf8"));

test("cada aba aponta para seu fluxo correto, independente da posição no HTML", () => {
  const expected = [
    ["cortar", "Cortar aula", "btnProcessar"],
    ["abertura", "Abertura de videocast", "btnAbertura"],
    ["letterings", "Indicar letterings", "btnLettering"],
    ["aprender", "Ensinar com esta edição", "btnAprender"],
    ["auditar", "Auditar edição final", "btnAuditar"],
  ];
  for (const [workflow, heading, button] of expected) {
    const expression = new RegExp(
      '<section class="bloco" data-workflow="' + workflow + '">[\\s\\S]*?<h2>' + heading +
      '</h2>[\\s\\S]*?id="' + button + '"'
    );
    assert.match(html, expression);
  }
  assert.match(source, /data-workflow="' \+ definicao\.id \+ '"/);
  assert.doesNotMatch(source, /const secoes = Array\.from\(document\.querySelectorAll/);
});

test("painel conecta ao backend local sem token nem acesso a valor nulo", async () => {
  assert.doesNotMatch(html, /localApiToken|Token do backend|LOCAL_API_TOKEN/);
  assert.doesNotMatch(source, /apiFetch|localApiToken|X-Assistente-Token/);
  assert.doesNotMatch(source, /Backend local indisponível/);
  assert.match(source, /const BACKEND_BASE_URL = "http:\/\/localhost:8000"/);
  assert.deepEqual(manifest.requiredPermissions.network.domains, ["http://localhost:8000"]);

  const calls = [];
  const context = vm.createContext({
    console,
    document: { readyState: "loading", addEventListener() {} },
    fetch(url, options) { calls.push({ url, options }); return Promise.resolve({ ok: true }); },
    require: (name) => name === "uxp" ? { storage: { localFileSystem: {} } } : {},
  });
  vm.runInContext(source, context);
  const options = { method: "POST", headers: { "Content-Type": "application/json" } };
  await context.backendFetch("http://localhost:8000/health", options);
  assert.deepEqual(calls, [{ url: "http://localhost:8000/health", options }]);
});
