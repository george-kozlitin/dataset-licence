// Live licence check for one Hub dataset: a port of dlf/licences.py and dlf/facts.py.
// The Python generator is the reference; tests/fixtures/parity.json pins both to the same answers.

const HF = "https://huggingface.co";
const API = `${HF}/api/datasets`;
export const MAX_SOURCES = 25;
const MAX_LICENCE_FILES = 3;
const CITATION_CAP = 1500;
const IGNORED_SOURCES = new Set(["original", "extended"]);
const LICENCE_FILE_RE = /^(licen[cs]e|copying)/i;
const NC_PHRASES = [
  "non-commercial", "noncommercial", "research only", "research purposes only", "research-only",
  "non commercial", "not for commercial use", "academic use only",
];
const NC_STAND_IN = "cc-by-nc-4.0";
const NC_REASON =
  "The dataset card's licence name or licence section contains wording that suggests " +
  "non-commercial, research-only or academic-only use restrictions.";

// -- licences.py ------------------------------------------------------------

const PERMISSIVE = new Set([
  "mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "cc0-1.0", "cc-by-4.0",
  "cc-by-3.0", "cc-by-2.0", "odc-by", "pddl", "unlicense", "isc",
]);
const SHARE_ALIKE = new Set([
  "cc-by-sa-4.0", "cc-by-sa-3.0", "odbl", "gpl-2.0", "gpl-3.0", "lgpl-3.0", "agpl-3.0", "gfdl",
]);
const RESTRICTIVE = new Set([
  "cc-by-nc-1.0", "cc-by-nc-2.0", "cc-by-nc-2.5", "cc-by-nc-3.0", "cc-by-nc-4.0",
  "cc-by-nc-sa-2.0", "cc-by-nc-sa-3.0", "cc-by-nc-sa-4.0", "cc-by-nc-nd-3.0", "cc-by-nc-nd-4.0",
  "openrail", "bigscience-openrail-m", "creativeml-openrail-m", "llama2", "llama3",
  "llama3.1", "llama3.2", "gemma", "c-uda", "cc-by-nd-4.0",
]);

function one(item: unknown): string | null {
  if (typeof item !== "string") return null;
  const s = item.trim().toLowerCase().replace(/[\s_]+/g, "-").replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");
  return s || null;
}

export function normalise(raw: unknown): string | null {
  if (Array.isArray(raw)) {
    for (const item of raw) {
      const s = one(item);
      if (s) return s;
    }
    return null;
  }
  return one(raw);
}

export function category(lic: string | null): string {
  if (lic === null) return "unclear";
  if (RESTRICTIVE.has(lic) || lic.startsWith("cc-by-nc")) return "restrictive";
  if (SHARE_ALIKE.has(lic)) return "share-alike";
  if (PERMISSIVE.has(lic) || lic.startsWith("bsd-")) return "permissive";
  return "unclear";
}

export function classify(stated: string | null, sources: (string | null)[], fileLicence: string | null): [string, string] {
  const tag = normalise(stated);
  const cat = category(tag);
  if (cat === "unclear") return ["unclear", "The checks could not identify a recognised licence ID in the dataset's metadata."];
  const fileLic = normalise(fileLicence);
  const fileCat = category(fileLic);
  if (fileCat !== "unclear" && fileCat !== cat) {
    return ["conflict",
      `The declared licence (${tag}) and the licence detected in a repository file ` +
      `(${fileLic}) fall into different categories, so the licences may conflict.`];
  }
  let unknown = false;
  for (const src of sources) {
    const s = normalise(src);
    const scat = category(s);
    if (scat === "unclear") unknown = true;
    else if (scat === "restrictive" && cat !== "restrictive") {
      return ["conflict",
        `The declared licence (${tag}) may conflict with a source dataset's reported ` +
        `licence (${s}), which is classified as having use restrictions.`];
    } else if (scat === "share-alike" && cat === "permissive") {
      return ["conflict",
        `The declared licence (${tag}) may conflict with a source dataset's reported ` +
        `share-alike licence (${s}).`];
    }
  }
  if (unknown && cat !== "restrictive") {
    return ["unclear", "The checks could not identify a recognised licence ID for at least one declared source dataset."];
  }
  if (cat === "restrictive") return ["restrictive", `The declared licence (${tag}) is classified as having use restrictions.`];
  if (cat === "share-alike") return ["permissive", `The declared licence (${tag}) is classified as share-alike.`];
  return ["permissive", `The declared licence (${tag}) is classified as permissive.`];
}

// -- licence text detection (facts.py) ---------------------------------------

const SPDX_RE = /SPDX-License-Identifier:\s*([A-Za-z0-9.+\-]+)/i;
const CC_LONG_RE = /Attribution(-NonCommercial)?(-ShareAlike|-NoDerivatives|-NoDerivs)?\s+(\d\.\d)/i;
const CC_SHORT_RE = /\bCC[ -]BY(-NC)?(-SA|-ND)?[ -](\d\.\d)/i;
const GPL_VER_RE = /version\s+([23])/i;

function cc(nc: string | undefined, rest: string | undefined, ver: string): string {
  let r = (rest ?? "").toLowerCase().replace(/^-+/, "");
  r = ({ sharealike: "sa", noderivatives: "nd", noderivs: "nd" } as Record<string, string>)[r] ?? r;
  return ["cc-by", ...(nc ? ["nc"] : []), ...(r ? [r] : []), ver].join("-");
}

export function detectLicenceText(text: string | null): string | null {
  if (!text) return null;
  const head = text.slice(0, 2048);
  const low = head.toLowerCase();
  let m = SPDX_RE.exec(head);
  if (m) return normalise(m[1]);
  if (low.includes("apache license") && /version\s+2\.0/.test(low)) return "apache-2.0";
  if (low.includes("creative commons zero") || low.includes("cc0 1.0")) return "cc0-1.0";
  m = CC_LONG_RE.exec(head);
  if (m) return cc(m[1], m[2], m[3]);
  m = CC_SHORT_RE.exec(head);
  if (m) return cc(m[1], m[2], m[3]);
  if (low.includes("gnu affero general public license")) return "agpl-3.0";
  if (low.includes("gnu lesser general public license")) return "lgpl-3.0";
  if (low.includes("gnu general public license")) {
    const v = GPL_VER_RE.exec(head);
    return v ? `gpl-${v[1]}.0` : null;
  }
  if (low.includes("redistribution and use in source and binary forms")) {
    return low.includes("neither the name") ? "bsd-3-clause" : "bsd-2-clause";
  }
  if (low.trimStart().startsWith("mit license") || low.includes("permission is hereby granted, free of charge")) return "mit";
  return null;
}

// -- card parsing --------------------------------------------------------------

const FRONT_RE = /^﻿?---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/;

export function cardBody(card: string | null): string {
  if (!card) return "";
  const m = FRONT_RE.exec(card);
  return m ? card.slice(m[0].length) : card;
}

const HEADING_RE = /^(#{1,6})\s+(.*?)\s*#*\s*$/;

function sections(body: string): [string, string][] {
  const out: [string, string[]][] = [];
  let fence = false;
  for (const line of body.split(/\r\n|\r|\n/)) {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    const m = fence ? null : HEADING_RE.exec(line);
    if (m) out.push([m[2], []]);
    else if (out.length) out[out.length - 1][1].push(line);
  }
  return out.map(([h, lines]) => [h, lines.join("\n")]);
}

function sectionText(body: string, pattern: RegExp): string {
  return sections(body).filter(([h]) => pattern.test(h)).map(([, t]) => t).join("\n");
}

export function citation(body: string): string | null {
  let text: string | null = null;
  for (const m of body.matchAll(/```[ \t]*([A-Za-z]*)[^\n]*\n([\s\S]*?)```/g)) {
    const lang = m[1].toLowerCase();
    const code = m[2].trim();
    if (lang === "bibtex" || (!lang && code.startsWith("@"))) {
      text = code;
      break;
    }
  }
  if (!text) text = sectionText(body, /citation|attribution/i).trim() || null;
  return text ? text.slice(0, CITATION_CAP) : null;
}

function asList(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === "string");
  return [];
}

const strOrNull = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export function entryLicence(entry: any, cardData: any): [string | null, string] {
  for (const tag of asList(entry?.tags)) {
    if (tag.toLowerCase().startsWith("license:")) {
      const lic = normalise(tag.split(":").slice(1).join(":"));
      if (lic) return [lic, "tag"];
    }
  }
  const lic = normalise(cardData?.license);
  return lic ? [lic, "card"] : [null, "none"];
}

export function sourceIds(cardData: any, ownId: string): string[] {
  const out: string[] = [];
  for (const raw of asList(cardData?.source_datasets)) {
    let s = raw.trim();
    if (s.toLowerCase().startsWith("extended|")) s = s.split("|").slice(1).join("|").trim();
    if (!s || IGNORED_SOURCES.has(s.toLowerCase()) || s.includes("://")) continue;
    if (s.toLowerCase() === ownId.toLowerCase() || out.includes(s)) continue;
    out.push(s);
  }
  return out;
}

const SOURCE_ID_RE = /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)?$/;
export const validId = (id: unknown): id is string =>
  typeof id === "string" && SOURCE_ID_RE.test(id) && id.split("/").every((p) => p !== "." && p !== "..");

function hasUnnegated(haystack: string, phrase: string): boolean {
  let start = 0;
  let i: number;
  while ((i = haystack.indexOf(phrase, start)) !== -1) {
    const before = (haystack.slice(0, i).match(/[a-z']+/g) ?? []).slice(-3);
    if (!before.includes("not") && !before.includes("no")) return true;
    start = i + 1;
  }
  return false;
}

export function customNc(stated: string | null, licenceName: string | null, body: string): boolean {
  if (stated !== null && stated !== "other" && stated !== "unknown") return false;
  const haystack = `${licenceName ?? ""}\n${sectionText(body, /licen[cs]e/i)}`.toLowerCase();
  return NC_PHRASES.some((p) => hasUnnegated(haystack, p));
}

function count(v: unknown): number {
  if (typeof v === "boolean") return 0;
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  return Number.isFinite(n) ? Math.max(0, Math.trunc(n)) : 0;
}

// -- evidence (evidence.py) ----------------------------------------------------

const repo = (id: string) => id.split("/").map(encodeURIComponent).join("/");
const rev = (r: string | null) => (r ? encodeURIComponent(r) : "main");
const encPath = (p: string) => encodeURI(p).replace(/[?#]/g, encodeURIComponent);
export const fileUrl = (id: string, r: string | null, path: string) => `${HF}/datasets/${repo(id)}/blob/${rev(r)}/${encPath(path)}`;
export const cardUrl = (id: string, r: string | null) => fileUrl(id, r, "README.md");
export const treeUrl = (id: string, r: string | null) => `${HF}/datasets/${repo(id)}/tree/${rev(r)}`;
export const apiUrl = (id: string, r: string | null) => `${HF}/api/datasets/${repo(id)}` + (r ? `/revision/${rev(r)}` : "");
export const pageUrl = (id: string) => `${HF}/datasets/${repo(id)}`;

// -- the check ------------------------------------------------------------------

export class HubUnavailable extends Error {}

export interface Source { id: string; licence: string | null; found: boolean; evidence: string | null }

const RECORD_FIELDS = ["cardData", "sha", "tags", "gated", "downloads", "likes", "lastModified"]
  .map((f) => `expand[]=${f}`).join("&");
const HEADERS = { "user-agent": "datalicense.uk live check" };

async function getJson(url: string): Promise<any | null> {
  const res = await fetch(url, { headers: HEADERS });
  if (res.status === 401 || res.status === 403 || res.status === 404) return null;
  if (!res.ok) throw new HubUnavailable(`${res.status} from the Hub`);
  return res.json();
}

async function getText(url: string, maxBytes?: number): Promise<string | null> {
  const headers: Record<string, string> = { ...HEADERS };
  if (maxBytes) headers.range = `bytes=0-${maxBytes - 1}`;
  const res = await fetch(url, { headers });
  if (res.status === 401 || res.status === 403 || res.status === 404) return null;
  if (!res.ok) throw new HubUnavailable(`${res.status} from the Hub`);
  return res.text();
}

/** Facts for one dataset, read live from the Hub; null when the dataset does not exist or is private. */
export async function checkDataset(id: string): Promise<Record<string, unknown> | null> {
  const record = await getJson(`${API}/${repo(id)}?${RECORD_FIELDS}`);
  if (record === null) return null;
  const canonical = strOrNull(record.id) ?? id;
  const sha = strOrNull(record.sha);
  const cardData = record.cardData && typeof record.cardData === "object" && !Array.isArray(record.cardData) ? record.cardData : {};
  const [tree, card] = await Promise.all([
    getJson(`${API}/${repo(canonical)}/tree/${rev(sha)}`),
    getText(`${HF}/datasets/${repo(canonical)}/resolve/${rev(sha)}/README.md`),
  ]);
  const body = cardBody(card);
  const [stated, statedFrom] = entryLicence(record, cardData);
  const licenceName = strOrNull(cardData.license_name);
  const licenceLink = strOrNull(cardData.license_link);

  const files = (Array.isArray(tree) ? tree : [])
    .filter((e: any) => e?.type === "file" && typeof e.path === "string" && !e.path.includes("/") && LICENCE_FILE_RE.test(e.path))
    .map((e: any) => e.path as string);
  let fileLicence: string | null = null;
  let fileRead: string | null = files[0] ?? null;
  for (const f of files.slice(0, MAX_LICENCE_FILES)) {
    fileLicence = detectLicenceText(await getText(`${HF}/datasets/${repo(canonical)}/resolve/${rev(sha)}/${encPath(f)}`, 2048));
    if (fileLicence) {
      fileRead = f;
      break;
    }
  }
  const fileEvidence = fileRead ? fileUrl(canonical, sha, fileRead) : treeUrl(canonical, sha);
  const statedEvidence = card !== null ? cardUrl(canonical, sha) : statedFrom !== "none" ? apiUrl(canonical, sha) : treeUrl(canonical, sha);

  const ids = sourceIds(cardData, canonical);
  const sources: Source[] = await Promise.all(ids.slice(0, MAX_SOURCES).map(async (sid): Promise<Source> => {
    const found = validId(sid) ? await getJson(`${API}/${repo(sid)}?expand[]=cardData&expand[]=tags&expand[]=sha`) : null;
    if (found === null) return { id: sid, licence: null, found: false, evidence: validId(sid) ? pageUrl(sid) : null };
    const cd = found.cardData && typeof found.cardData === "object" ? found.cardData : {};
    const [lic] = entryLicence(found, cd);
    return { id: sid, licence: lic, found: true, evidence: cardUrl(strOrNull(found.id) ?? sid, strOrNull(found.sha)) };
  }));

  const nc = customNc(stated, licenceName, body);
  let [signal, reason] = classify(nc ? NC_STAND_IN : stated, sources.map((s) => s.licence), fileLicence);
  if (nc) {
    reason = signal === "restrictive"
      ? NC_REASON
      : reason.replace(`The declared licence (${NC_STAND_IN})`, "The custom licence described in the dataset card");
  }
  const gated = typeof record.gated === "boolean" || typeof record.gated === "string" ? record.gated : false;
  const slash = canonical.lastIndexOf("/");
  return {
    id: canonical,
    owner: slash > 0 ? canonical.slice(0, slash) : null,
    name: canonical.slice(slash + 1),
    downloads: count(record.downloads),
    likes: count(record.likes),
    last_modified: String(record.lastModified ?? ""),
    stated_licence: stated,
    stated_from: statedFrom,
    licence_name: licenceName,
    licence_link: licenceLink,
    file_licence: fileLicence,
    licence_files: files,
    sources,
    sources_omitted: Math.max(0, ids.length - MAX_SOURCES),
    gated,
    tasks: asList(cardData.task_categories),
    citation: citation(body),
    signal,
    reason,
    revision: sha,
    stated_evidence: statedEvidence,
    file_evidence: fileEvidence,
    card_nc: nc,
  };
}
