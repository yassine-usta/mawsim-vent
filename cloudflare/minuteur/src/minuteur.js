// Minuteur de l'atelier de Mawsim : la logique (index.js n'est que l'entrée du Worker, qui ne peut exporter que ses
// gestionnaires). Un Worker de Cloudflare lancé toutes les cinq minutes par un cron.
//
// Il lit le latest.json d'ICON (et de CAMS) chez Open-Meteo, le compare au calcul publié (index.json, lu par une
// liaison de service vers le Worker de l'index, sans adresse publique), et lance vent.yml sur GitHub par
// workflow_dispatch quand un calcul complet n'est pas encore en ligne.
//
// Garde-fous :
//   - il ne lance rien si un passage de vent.yml est en attente ou en cours ;
//   - il ne lance pas plus de MAX_ESSAIS passages pour un même calcul (sur les dernières FENETRE_HEURES), succès
//     compris : un passage qui ne met pas le calcul en ligne n'est pas relancé en boucle ;
//   - il attend ATTENTE_MINUTES après un essai avant le suivant.
// Chaque passage qu'il lance porte dans son titre la clé du calcul ("icon 2026-11-22T06:00Z") : c'est ce qui permet de
// compter les essais sans rien stocker.
//
// Secrets du Worker (rangés par l'utilisateur, jamais dans le dépôt) : GITHUB_TOKEN (jeton limité au dépôt de
// l'atelier, droit Actions en lecture et écriture) et DEPOT ("propriétaire/nom" du dépôt).
// Variables (wrangler.toml) : SOURCE, GITHUB_API, BRANCHE, WORKFLOW.

export const MAX_ESSAIS = 2;
export const FENETRE_HEURES = 12;
export const ATTENTE_MINUTES = 30;
const EN_COURS = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

/** "2026-11-22T06:00:00Z" ou "2026-11-22T06:00Z" -> "2026-11-22T06:00Z" (la forme de l'index). */
export function heure(texte) {
  if (typeof texte !== "string" || texte.length < 16) return null;
  return `${texte.slice(0, 16)}Z`;
}

function plusRecent(a, b) {
  // Les heures ont toutes la même forme : l'ordre des textes est celui du temps.
  return a !== null && (b === null || a > b);
}

/**
 * Ce qu'il faut faire, sans réseau. Rend {action: "rien" | "attendre" | "abandon" | "lancer", raison, cle?, forcer?}.
 *
 * icon, cams : les latest.json d'Open-Meteo (ou null s'ils sont illisibles).
 * index : l'index publié (ou null : premier passage, ou index injoignable).
 * passages : les derniers passages de vent.yml (API de GitHub : status, conclusion, display_title, created_at).
 * maintenant : une Date.
 */
export function decider({ icon, cams, index, passages, maintenant }) {
  const enCours = passages.find((p) => EN_COURS.has(p.status));
  if (enCours) return { action: "attendre", raison: `passage en cours (${enCours.display_title})` };

  let cible = null;
  const publie = index ? heure(index.run) : null;
  const calculIcon = icon && icon.completed === true ? heure(icon.reference_time) : null;
  if (plusRecent(calculIcon, publie)) {
    cible = { cle: `icon ${calculIcon}`, forcer: false, raison: `ICON ${calculIcon} complet, en ligne : ${publie ?? "rien"}` };
  } else if (index && cams && cams.completed === true) {
    // CAMS a son propre calcul (0 h et 12 h UTC). L'atelier le prend à chaque passage, mais le contrôle de vent.yml ne
    // regarde que ICON : un calcul de CAMS plus récent que celui de la poussière ou de l'UV publiés demande un passage forcé.
    const calculCams = heure(cams.reference_time);
    const couches = index.layers || {};
    const publies = ["dust", "uv"].map((cle) => (couches[cle] ? heure(couches[cle].run) : null));
    const plusVieux = publies.includes(null) ? null : publies.sort()[0];
    if (plusRecent(calculCams, plusVieux)) {
      cible = { cle: `cams ${calculCams}`, forcer: true, raison: `CAMS ${calculCams} complet, en ligne : ${plusVieux ?? "rien"}` };
    }
  }
  if (!cible) return { action: "rien", raison: `à jour (ICON en ligne : ${publie ?? "rien"})` };

  const depuis = maintenant.getTime() - FENETRE_HEURES * 3600e3;
  const essais = passages
    .filter((p) => typeof p.display_title === "string" && p.display_title.includes(cible.cle))
    .filter((p) => Date.parse(p.created_at) >= depuis)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  if (essais.length >= MAX_ESSAIS) {
    return { action: "abandon", raison: `${cible.raison} ; déjà ${essais.length} essais, plus de relance`, cle: cible.cle };
  }
  if (essais.length && maintenant.getTime() - Date.parse(essais[0].created_at) < ATTENTE_MINUTES * 60e3) {
    return { action: "attendre", raison: `${cible.raison} ; dernier essai il y a moins de ${ATTENTE_MINUTES} min`, cle: cible.cle };
  }
  return { action: "lancer", raison: cible.raison, cle: cible.cle, forcer: cible.forcer };
}

async function lireJson(reponse, quoi) {
  if (!reponse.ok) throw new Error(`${quoi} : ${reponse.status}`);
  return reponse.json();
}

async function lireOuNull(promesse, quoi) {
  try {
    return await lireJson(await promesse, quoi);
  } catch (erreur) {
    console.log(`${quoi} illisible (${erreur.message})`);
    return null;
  }
}

function enTetesGithub(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "mawsim-minuteur",
  };
}

/** Un tour du minuteur : lit, décide, lance au besoin. Rend la décision (pour les journaux et les essais). */
export async function tour(env, maintenant = new Date()) {
  if (!env.GITHUB_TOKEN || !env.DEPOT) throw new Error("secrets GITHUB_TOKEN et DEPOT absents");
  const workflow = `${env.GITHUB_API}/repos/${env.DEPOT}/actions/workflows/${env.WORKFLOW}`;
  const [icon, cams, index, liste] = await Promise.all([
    lireOuNull(fetch(`${env.SOURCE}/dwd_icon/latest.json`), "latest.json d'ICON"),
    lireOuNull(fetch(`${env.SOURCE}/cams_global/latest.json`), "latest.json de CAMS"),
    lireOuNull(env.INDEX.fetch("https://index/index.json"), "index publié"),
    // Les passages ne sont pas facultatifs : sans eux, on ne sait pas si un passage tourne déjà.
    fetch(`${workflow}/runs?per_page=30`, { headers: enTetesGithub(env) }).then((r) => lireJson(r, "liste des passages")),
  ]);
  const decision = decider({ icon, cams, index, passages: liste.workflow_runs || [], maintenant });
  console.log(`${decision.action} : ${decision.raison}`);
  if (decision.action !== "lancer") return decision;

  const reponse = await fetch(`${workflow}/dispatches`, {
    method: "POST",
    headers: { ...enTetesGithub(env), "Content-Type": "application/json" },
    body: JSON.stringify({ ref: env.BRANCHE, inputs: { forcer: String(decision.forcer), calcul: decision.cle } }),
  });
  if (!reponse.ok) throw new Error(`lancement refusé par GitHub : ${reponse.status} ${await reponse.text()}`);
  console.log(`vent.yml lancé (${decision.cle}, forcer : ${decision.forcer})`);
  return decision;
}
