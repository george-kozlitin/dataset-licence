// Dataset Licence MCP Worker: stateless MCP over streamable HTTP (JSON responses).
// Implements initialize, tools/list and tools/call as plain JSON-RPC 2.0 on POST /mcp.

import { checkDataset, HubUnavailable, MAX_SOURCES } from "./check";

export interface Env {
  SITE_URL: string;
  CHECK_LIMIT?: { limit(o: { key: string }): Promise<{ success: boolean }> };
  STATS: KVNamespace;
  MCP_SALT: string;
  MCP_STATS_SECRET: string;
}

const DISCLAIMER = "Licence facts reported by the dataset and its declared sources. Not legal advice, and not a grant of permission to use the dataset.";
const ID_RE = /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)?$/;
const SIGNALS = ["permissive", "restrictive", "conflict", "unclear"];
const SIGNAL_HELP =
  "Signal values: permissive = permissive or share-alike licence reported; restrictive = use restrictions reported; conflict = possible licence conflict; unclear = insufficient or unrecognised licence information. These signals do not establish permission to use a dataset.";
const INDEX_TTL_MS = 10 * 60 * 1000;
const COUNT_TTL_S = 70 * 86400;
const MAX_LIMIT = 50;
const LIVE_TTL_S = 6 * 3600;
const LIVE_NOTE =
  `Checked live on the Hugging Face Hub because the dataset is not in the weekly index, or a fresh check was asked for. At most ${MAX_SOURCES} declared sources are looked up; sources_omitted counts the rest.`;
const PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];

const CORS: Record<string, string> = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "POST, GET, OPTIONS",
  "access-control-allow-headers": "content-type, accept, mcp-protocol-version, mcp-session-id",
  "access-control-expose-headers": "mcp-session-id",
};

function validId(id: unknown): id is string {
  return typeof id === "string" && ID_RE.test(id) && id.split("/").every((s) => !/^\.+$/.test(s));
}

type Row = [string, string, string | null, string[], number, (string | null)?];
let indexCache: { at: number; rows: Row[] } | null = null;
export function _resetCache(): void { indexCache = null; }

const TOOLS = [
  {
    name: "dataset_licence",
    description:
      "Retrieve licence facts for one Hugging Face dataset: declared licence, where it was declared, recorded source datasets, detected licence files and a signal. Each licence value comes with an evidence URL linking to the Hub file, API record, file list or Hub page that was checked, with a commit-specific link where available. Datasets in the weekly index of popular datasets are answered from it; any other public dataset is checked live on the Hub. " + SIGNAL_HELP + " " + DISCLAIMER,
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Dataset ID, for example 'owner/name'." },
        live: { type: "boolean", description: "Check the Hub now instead of using the weekly index. Datasets outside the index are always checked live." },
      },
      required: ["id"],
      additionalProperties: false,
    },
  },
  {
    name: "find_datasets",
    description:
      "List popular Hugging Face datasets from the published index, ordered by download count from highest to lowest. Optionally filter by signal, task tag and declared licence ID; all supplied filters must match. Returns up to 50 results; licence_evidence links to the Hub file, API record or file list checked for a declared licence. " + SIGNAL_HELP + " " + DISCLAIMER,
    inputSchema: {
      type: "object",
      properties: {
        signal: { type: "string", enum: SIGNALS, description: SIGNAL_HELP },
        task: { type: "string", description: "Task tag, for example 'text-generation'." },
        licence: { type: "string", description: "Declared licence ID, for example 'apache-2.0'." },
        limit: { type: "integer", minimum: 1, maximum: MAX_LIMIT, description: "Default 20, maximum 50." },
      },
      additionalProperties: false,
    },
  },
];

function base(env: Env): string { return env.SITE_URL.replace(/\/+$/, ""); }

async function getIndex(env: Env): Promise<Row[]> {
  const now = Date.now();
  if (indexCache && now - indexCache.at < INDEX_TTL_MS) return indexCache.rows;
  const res = await fetch(base(env) + "/data/index.json");
  if (!res.ok) throw new Error("index unavailable");
  const rows = (await res.json()) as Row[];
  if (!Array.isArray(rows)) throw new Error("index malformed");
  indexCache = { at: now, rows };
  return rows;
}

const toolText = (text: string, isError = false) => ({
  content: [{ type: "text", text }],
  ...(isError ? { isError: true } : {}),
});

/** Live facts for one dataset, from the Cache API when a check ran in the last few hours. */
export async function liveFacts(id: string, ctx?: ExecutionContext): Promise<{ facts: Record<string, unknown>; checked_at: string } | null> {
  const cache = typeof caches !== "undefined" ? (caches as any).default as Cache : null;
  const key = new Request(`https://live-check.internal/${encodeURIComponent(id.toLowerCase())}`);
  const hit = cache ? await cache.match(key) : undefined;
  if (hit) return hit.json();
  const facts = await checkDataset(id);
  if (facts === null) return null;
  const out = { facts, checked_at: new Date().toISOString() };
  if (cache) {
    const put = cache.put(key, new Response(JSON.stringify(out), { headers: { "cache-control": `max-age=${LIVE_TTL_S}` } }));
    if (ctx?.waitUntil) ctx.waitUntil(put); else await put;
  }
  return out;
}

async function allowed(env: Env, req: Request): Promise<boolean> {
  if (!env.CHECK_LIMIT) return true;
  try {
    return (await env.CHECK_LIMIT.limit({ key: req.headers.get("CF-Connecting-IP") ?? "unknown" })).success;
  } catch {
    return true;
  }
}

async function liveAnswer(env: Env, id: string, req: Request, ctx?: ExecutionContext) {
  if (!(await allowed(env, req))) return toolText("Too many live checks from this address. Try again in a minute.", true);
  let got;
  try { got = await liveFacts(id, ctx); } catch (e) {
    return toolText(e instanceof HubUnavailable ? "The Hugging Face Hub is not answering right now. Try again later." : "The live check failed. Try again later.", true);
  }
  if (got === null) return toolText(`Dataset '${id}' was not found on the Hugging Face Hub, or it is private.`, true);
  return toolText(JSON.stringify({ facts: got.facts, source: "live check", checked_at: got.checked_at, note: LIVE_NOTE, disclaimer: DISCLAIMER }, null, 2));
}

async function datasetLicence(env: Env, args: any, req: Request, ctx?: ExecutionContext) {
  const id = args?.id;
  if (!validId(id)) return toolText("Invalid dataset ID. Expected 'owner/name' or 'name'.", true);
  if (args?.live !== undefined && typeof args.live !== "boolean") return toolText("live must be true or false.", true);
  if (args?.live) return liveAnswer(env, id, req, ctx);
  let res: Response;
  try { res = await fetch(`${base(env)}/data/d/${id}.json`); } catch { return toolText("Facts are temporarily unavailable.", true); }
  if (res.status === 404) return liveAnswer(env, id, req, ctx);
  if (!res.ok) return toolText("Facts are temporarily unavailable.", true);
  let facts: unknown;
  try { facts = await res.json(); } catch { return toolText(`Could not read the published facts for dataset '${id}'. Please try again later.`, true); }
  return toolText(JSON.stringify({ facts, source: "weekly index", disclaimer: DISCLAIMER, page: `${base(env)}/d/${id}/` }, null, 2));
}

async function handleCheck(req: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
  const id = new URL(req.url).searchParams.get("id") ?? "";
  const headers = { "content-type": "application/json", "access-control-allow-origin": "*", "cache-control": "no-store" };
  const reply = (body: unknown, status: number) => new Response(JSON.stringify(body), { status, headers });
  if (!validId(id)) return reply({ error: "invalid_id", message: "Expected a dataset ID such as 'owner/name'." }, 400);
  if (!(await allowed(env, req))) return reply({ error: "rate_limited", message: "Too many live checks from this address. Try again in a minute." }, 429);
  let got;
  try { got = await liveFacts(id, ctx); } catch (e) {
    return reply({ error: "hub_unavailable", message: e instanceof HubUnavailable ? "The Hugging Face Hub is not answering right now. Try again later." : "The live check failed. Try again later." }, 502);
  }
  if (got === null) return reply({ error: "not_found", message: "This dataset was not found on the Hugging Face Hub, or it is private." }, 404);
  return reply({ ...got, source: "live check", note: LIVE_NOTE, disclaimer: DISCLAIMER }, 200);
}

async function findDatasets(env: Env, args: any) {
  const a = args ?? {};
  if (a.signal !== undefined && !SIGNALS.includes(a.signal)) return toolText(`signal must be one of: ${SIGNALS.join(", ")}.`, true);
  for (const k of ["task", "licence"]) if (a[k] !== undefined && typeof a[k] !== "string") return toolText(`${k} must be a string.`, true);
  if (a.limit !== undefined && (typeof a.limit === "boolean" || a.limit === "" || !Number.isInteger(Number(a.limit)) || Number(a.limit) < 1)) return toolText("limit must be a positive integer.", true);
  const limit = Math.min(a.limit === undefined ? 20 : Number(a.limit), MAX_LIMIT);
  let rows: Row[];
  try { rows = await getIndex(env); } catch { return toolText("The dataset index is temporarily unavailable.", true); }
  const lic = a.licence?.toLowerCase();
  const out = rows
    .filter((r) => (a.signal === undefined || r[1] === a.signal) &&
      (a.task === undefined || (r[3] ?? []).includes(a.task)) &&
      (lic === undefined || (r[2] ?? "").toLowerCase() === lic))
    .sort((x, y) => y[4] - x[4])
    .slice(0, limit)
    .map(([id, signal, stated_licence, tasks, downloads, licence_evidence]) => ({
      id, signal, stated_licence, licence_evidence: licence_evidence ?? null, tasks, downloads,
      page: `${base(env)}/d/${id}/`,
    }));
  return toolText(JSON.stringify({ datasets: out, count: out.length, disclaimer: DISCLAIMER }, null, 2));
}

function isoWeek(d: Date): string {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const year = t.getUTCFullYear();
  const week = Math.ceil(((t.getTime() - Date.UTC(year, 0, 1)) / 86400000 + 1) / 7);
  return `${year}-W${String(week).padStart(2, "0")}`;
}

async function countClient(env: Env, req: Request): Promise<void> {
  try {
    const ip = req.headers.get("CF-Connecting-IP");
    if (!ip) return;
    const week = isoWeek(new Date());
    const data = new TextEncoder().encode(env.MCP_SALT + ip + week);
    const hex = [...new Uint8Array(await crypto.subtle.digest("SHA-256", data))]
      .map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
    const key = `w:${week}:${hex}`;
    if ((await env.STATS.get(key)) === null) await env.STATS.put(key, "1", { expirationTtl: COUNT_TTL_S });
  } catch {
    // Counting must never break a tool call.
  }
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const rpcError = (id: unknown, code: number, message: string, status = 200) =>
  json({ jsonrpc: "2.0", id: id ?? null, error: { code, message } }, status);

async function handleRpc(req: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
  let msg: any;
  try { msg = await req.json(); } catch { return rpcError(null, -32700, "Parse error", 400); }
  if (!msg || typeof msg !== "object" || Array.isArray(msg) || typeof msg.method !== "string") {
    return rpcError(msg?.id, -32600, "Invalid request", 400);
  }
  if (msg.id === undefined || msg.id === null) return new Response(null, { status: 202 }); // notification
  switch (msg.method) {
    case "initialize": {
      const asked = msg.params?.protocolVersion;
      return json({
        jsonrpc: "2.0", id: msg.id,
        result: {
          protocolVersion: PROTOCOL_VERSIONS.includes(asked) ? asked : PROTOCOL_VERSIONS[0],
          capabilities: { tools: {} },
          serverInfo: { name: "dataset-licence-mcp", version: "0.2.0" },
          instructions: DISCLAIMER,
        },
      });
    }
    case "ping":
      return json({ jsonrpc: "2.0", id: msg.id, result: {} });
    case "tools/list":
      return json({ jsonrpc: "2.0", id: msg.id, result: { tools: TOOLS } });
    case "resources/list":
      return json({ jsonrpc: "2.0", id: msg.id, result: { resources: [] } });
    case "prompts/list":
      return json({ jsonrpc: "2.0", id: msg.id, result: { prompts: [] } });
    case "tools/call": {
      if (ctx?.waitUntil) ctx.waitUntil(countClient(env, req));
      else await countClient(env, req);
      const name = msg.params?.name;
      const args = msg.params?.arguments;
      let result;
      if (name === "dataset_licence") result = await datasetLicence(env, args, req, ctx);
      else if (name === "find_datasets") result = await findDatasets(env, args);
      else result = toolText(`Unknown tool '${String(name)}'.`, true);
      return json({ jsonrpc: "2.0", id: msg.id, result });
    }
    default:
      return rpcError(msg.id, -32601, "Method not found");
  }
}

async function handleStats(req: Request, env: Env): Promise<Response> {
  const given = req.headers.get("authorization") ?? "";
  const want = `Bearer ${env.MCP_STATS_SECRET}`;
  if (!env.MCP_STATS_SECRET || given.length !== want.length || !timingEqual(given, want)) {
    return new Response("Unauthorized", { status: 401 });
  }
  const weeks: Record<string, number> = {};
  let cursor: string | undefined;
  do {
    const page: any = await env.STATS.list({ prefix: "w:", cursor });
    for (const k of page.keys) {
      const week = k.name.split(":")[1];
      weeks[week] = (weeks[week] ?? 0) + 1;
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return json({ weeks });
}

function timingEqual(a: string, b: string): boolean {
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export default {
  async fetch(req: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
    const { pathname } = new URL(req.url);
    if (pathname === "/mcp") {
      let res: Response;
      if (req.method === "OPTIONS") res = new Response(null, { status: 204 });
      else if (req.method !== "POST") res = new Response("Method Not Allowed", { status: 405, headers: { allow: "POST" } });
      else res = await handleRpc(req, env, ctx);
      for (const [k, v] of Object.entries(CORS)) res.headers.set(k, v);
      return res;
    }
    if (pathname === "/stats" && req.method === "GET") return handleStats(req, env);
    if (pathname === "/v1/check" && req.method === "GET") return handleCheck(req, env, ctx);
    if (pathname === "/" && req.method === "GET") {
      return new Response(
        `Dataset Licence MCP server. Site: ${base(env)}/\nMCP endpoint (streamable HTTP, POST): /mcp\n${DISCLAIMER}\n`,
        { headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }
    return new Response("Not found", { status: 404 });
  },
};
