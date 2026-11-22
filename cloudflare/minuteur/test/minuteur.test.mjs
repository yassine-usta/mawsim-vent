// Essais sans réseau du minuteur : node --test cloudflare/minuteur/test
import assert from "node:assert/strict";
import { test } from "node:test";

import { ATTENTE_MINUTES, MAX_ESSAIS, decider, heure, tour } from "../src/minuteur.js";

const MAINTENANT = new Date("2026-11-22T10:00:00Z");
const icon = (ref, completed = true) => ({ completed, reference_time: ref });
const index = (run, dust = "2026-11-22T00:00Z", uv = "2026-11-22T00:00Z") => ({ run, layers: { dust: { run: dust }, uv: { run: uv } } });
const passage = (titre, minutes, status = "completed", conclusion = "success") => ({
  display_title: titre, status, conclusion, created_at: new Date(MAINTENANT.getTime() - minutes * 60e3).toISOString(),
});
const CAMS = icon("2026-11-22T00:00:00Z");

test("les heures prennent la forme de l'index", () => {
  assert.equal(heure("2026-11-22T06:00:00Z"), "2026-11-22T06:00Z");
  assert.equal(heure("2026-11-22T06:00Z"), "2026-11-22T06:00Z");
  assert.equal(heure(undefined), null);
});

test("un calcul complet d'ICON plus récent que l'index est lancé, sans forcer", () => {
  const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: CAMS, index: index("2026-11-22T00:00Z"), passages: [], maintenant: MAINTENANT });
  assert.equal(d.action, "lancer");
  assert.equal(d.cle, "icon 2026-11-22T06:00Z");
  assert.equal(d.forcer, false);
});

test("un calcul pas encore complet n'est pas lancé", () => {
  const d = decider({ icon: icon("2026-11-22T06:00:00Z", false), cams: CAMS, index: index("2026-11-22T00:00Z"), passages: [], maintenant: MAINTENANT });
  assert.equal(d.action, "rien");
});

test("un calcul déjà en ligne ne lance rien", () => {
  const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: CAMS, index: index("2026-11-22T06:00Z"), passages: [], maintenant: MAINTENANT });
  assert.equal(d.action, "rien");
});

test("un index plus récent que latest.json ne lance rien", () => {
  const d = decider({ icon: icon("2026-11-22T00:00:00Z"), cams: CAMS, index: index("2026-11-22T06:00Z"), passages: [], maintenant: MAINTENANT });
  assert.equal(d.action, "rien");
});

test("sans index publié (premier passage, ou index injoignable), le calcul complet est lancé", () => {
  const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: CAMS, index: null, passages: [], maintenant: MAINTENANT });
  assert.equal(d.action, "lancer");
});

test("Open-Meteo injoignable : rien", () => {
  const d = decider({ icon: null, cams: null, index: index("2026-11-22T00:00Z"), passages: [], maintenant: MAINTENANT });
  assert.equal(d.action, "rien");
});

for (const status of ["queued", "in_progress", "waiting", "requested", "pending"]) {
  test(`un passage ${status} n'est jamais doublé`, () => {
    const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: CAMS, index: index("2026-11-22T00:00Z"),
      passages: [passage("Vent", 3, status, null)], maintenant: MAINTENANT });
    assert.equal(d.action, "attendre");
  });
}

test(`pas plus de ${MAX_ESSAIS} essais pour un même calcul, échecs ou succès`, () => {
  const passages = [passage("Vent, icon 2026-11-22T06:00Z", 40, "completed", "failure"), passage("Vent, icon 2026-11-22T06:00Z", 120, "completed", "success")];
  const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: CAMS, index: index("2026-11-22T00:00Z"), passages, maintenant: MAINTENANT });
  assert.equal(d.action, "abandon");
});

test(`un second essai attend ${ATTENTE_MINUTES} minutes après le premier`, () => {
  const tot = [passage("Vent, icon 2026-11-22T06:00Z", 10, "completed", "failure")];
  assert.equal(decider({ icon: icon("2026-11-22T06:00:00Z"), cams: CAMS, index: index("2026-11-22T00:00Z"), passages: tot, maintenant: MAINTENANT }).action, "attendre");
  const tard = [passage("Vent, icon 2026-11-22T06:00Z", ATTENTE_MINUTES + 1, "completed", "failure")];
  assert.equal(decider({ icon: icon("2026-11-22T06:00:00Z"), cams: CAMS, index: index("2026-11-22T00:00Z"), passages: tard, maintenant: MAINTENANT }).action, "lancer");
});

test("les essais d'un autre calcul ou trop anciens ne comptent pas", () => {
  const passages = [passage("Vent, icon 2026-11-22T00:00Z", 60, "completed", "failure"), passage("Vent, icon 2026-11-22T00:00Z", 90, "completed", "failure"),
    passage("Vent, icon 2026-11-22T06:00Z", 13 * 60, "completed", "failure"), passage("Vent, icon 2026-11-22T06:00Z", 14 * 60, "completed", "failure")];
  const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: CAMS, index: index("2026-11-22T00:00Z"), passages, maintenant: MAINTENANT });
  assert.equal(d.action, "lancer");
});

test("un calcul de CAMS plus récent que la poussière ou l'UV publiés force un passage", () => {
  const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: icon("2026-11-22T12:00:00Z"), index: index("2026-11-22T06:00Z", "2026-11-22T00:00Z", "2026-11-22T12:00Z"),
    passages: [], maintenant: MAINTENANT });
  assert.equal(d.action, "lancer");
  assert.equal(d.cle, "cams 2026-11-22T12:00Z");
  assert.equal(d.forcer, true);
});

test("une famille de CAMS absente de l'index compte comme en retard", () => {
  const sansUv = { run: "2026-11-22T06:00Z", layers: { dust: { run: "2026-11-22T12:00Z" } } };
  const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: icon("2026-11-22T12:00:00Z"), index: sansUv, passages: [], maintenant: MAINTENANT });
  assert.equal(d.cle, "cams 2026-11-22T12:00Z");
});

test("CAMS à jour et ICON à jour : rien", () => {
  const d = decider({ icon: icon("2026-11-22T06:00:00Z"), cams: icon("2026-11-22T00:00:00Z"), index: index("2026-11-22T06:00Z"), passages: [], maintenant: MAINTENANT });
  assert.equal(d.action, "rien");
});

test("ICON passe avant CAMS", () => {
  const d = decider({ icon: icon("2026-11-22T12:00:00Z"), cams: icon("2026-11-22T12:00:00Z"), index: index("2026-11-22T06:00Z"), passages: [], maintenant: MAINTENANT });
  assert.equal(d.cle, "icon 2026-11-22T12:00Z");
});

// Un tour entier, avec de faux serveurs : aucune requête ne sort.
function fauxServeurs({ latest, cams, publie, passages, statutLancement = 204 }) {
  const appels = [];
  const repondre = (corps, status = 200) => new Response(JSON.stringify(corps), { status, headers: { "Content-Type": "application/json" } });
  globalThis.fetch = async (url, options = {}) => {
    appels.push({ url: String(url), options });
    if (url.endsWith("/dwd_icon/latest.json")) return repondre(latest);
    if (url.endsWith("/cams_global/latest.json")) return repondre(cams);
    if (url.includes("/runs?")) return repondre({ workflow_runs: passages });
    if (url.endsWith("/dispatches")) return new Response(null, { status: statutLancement });
    throw new Error(`adresse inattendue : ${url}`);
  };
  const env = {
    SOURCE: "https://source.test/data_spatial", GITHUB_API: "https://github.test", BRANCHE: "main", WORKFLOW: "vent.yml",
    GITHUB_TOKEN: "jeton-de-test", DEPOT: "proprietaire/depot",
    INDEX: { fetch: async () => (publie ? repondre(publie) : new Response("absent", { status: 404 })) },
  };
  return { appels, env };
}

test("un tour lance vent.yml avec la clé du calcul et le jeton", async () => {
  const { appels, env } = fauxServeurs({ latest: icon("2026-11-22T06:00:00Z"), cams: CAMS, publie: index("2026-11-22T00:00Z"), passages: [] });
  const d = await tour(env, MAINTENANT);
  assert.equal(d.action, "lancer");
  const lancement = appels.find((a) => a.url.endsWith("/dispatches"));
  assert.equal(lancement.url, "https://github.test/repos/proprietaire/depot/actions/workflows/vent.yml/dispatches");
  assert.equal(lancement.options.method, "POST");
  assert.equal(lancement.options.headers.Authorization, "Bearer jeton-de-test");
  assert.ok(lancement.options.headers["User-Agent"]);
  assert.deepEqual(JSON.parse(lancement.options.body), { ref: "main", inputs: { forcer: "false", calcul: "icon 2026-11-22T06:00Z" } });
});

test("un tour à jour ne lance rien", async () => {
  const { appels, env } = fauxServeurs({ latest: icon("2026-11-22T06:00:00Z"), cams: CAMS, publie: index("2026-11-22T06:00Z"), passages: [] });
  assert.equal((await tour(env, MAINTENANT)).action, "rien");
  assert.ok(!appels.some((a) => a.url.endsWith("/dispatches")));
});

test("un lancement refusé par GitHub est une erreur (visible dans les journaux du Worker)", async () => {
  const { env } = fauxServeurs({ latest: icon("2026-11-22T06:00:00Z"), cams: CAMS, publie: index("2026-11-22T00:00Z"), passages: [], statutLancement: 403 });
  await assert.rejects(tour(env, MAINTENANT), /refusé par GitHub : 403/);
});

test("sans secrets, le tour s'arrête avant toute requête", async () => {
  const { appels, env } = fauxServeurs({ latest: icon("2026-11-22T06:00:00Z"), cams: CAMS, publie: null, passages: [] });
  delete env.GITHUB_TOKEN;
  await assert.rejects(tour(env, MAINTENANT), /secrets/);
  assert.equal(appels.length, 0);
});

test("l'entrée du Worker n'exporte que son gestionnaire (workerd refuse tout autre export)", async () => {
  const entree = await import("../src/index.js");
  assert.deepEqual(Object.keys(entree), ["default"]);
  assert.equal(typeof entree.default.scheduled, "function");
});
