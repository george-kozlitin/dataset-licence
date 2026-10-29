import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import worker, { _resetCache } from "../src/index";

const fx = (p: string) => readFileSync(fileURLToPath(new URL("./fixtures/" + p, import.meta.url)), "utf8");
const SITE = "https://site.test";
const DISCLAIMER = "Licence facts reported by the dataset and its declared sources. Not legal advice, and not a grant of permission to use the dataset.";

class MemKV {
  store = new Map<string, { value: string; ttl?: number }>();
  puts = 0;
  async get(k: string) { return this.store.get(k)?.value ?? null; }
  async put(k: string, v: string, o?: { expirationTtl?: number }) { this.puts++; this.store.set(k, { value: v, ttl: o?.expirationTtl }); }
  lists = 0;
  async list({ prefix, cursor }: { prefix: string; cursor?: string }) {
    this.lists++;
    const all = [...this.store.keys()].filter((k) => k.startsWith(prefix));
    const start = cursor ? Number(cursor) : 0;
    const end = start + 2;
    return { keys: all.slice(start, end).map((name) => ({ name })), list_complete: end >= all.length, cursor: String(end) };
  }
}

let kv: MemKV;
let fetched: string[];
const env = () => ({ SITE_URL: SITE, STATS: kv, MCP_SALT: "salt", MCP_STATS_SECRET: "s3cret" });

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

beforeEach(() => {
  kv = new MemKV();
  fetched = [];
  _resetCache();
  globalThis.fetch = (async (input: any) => {
    const url = String(typeof input === "string" ? input : input.url);
    fetched.push(url);
    const path = url.slice(SITE.length);
    if (path === "/data/index.json") return new Response(fx("index.json"));
    const m = path.match(/^\/data\/d\/(.+)\.json$/);
    if (m) {
      try { return new Response(fx("d/" + decodeURIComponent(m[1]) + ".json")); } catch { return new Response("nope", { status: 404 }); }
    }
    return new Response("nope", { status: 404 });
  }) as any;
});

async function rpc(method: string, params: any = {}, ip = "1.2.3.4", id: any = 1) {
  const req = new Request("https://w.test/mcp", {
    method: "POST",
    headers: { "content-type": "application/json", "CF-Connecting-IP": ip, accept: "application/json, text/event-stream" },
    body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
  });
  const res = await worker.fetch(req, env() as any, {} as any);
  return { res, body: res.status === 200 ? await res.json() : null } as any;
}
const call = (name: string, args: any, ip?: string) => rpc("tools/call", { name, arguments: args }, ip);

describe("protocol", () => {
  it("initialize", async () => {
    const { body } = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t", version: "1" } });
    expect(body.result.serverInfo.name).toBe("dataset-licence-mcp");
    expect(body.result.capabilities.tools).toBeDefined();
  });
  it("tools/list returns both tools", async () => {
    const { body } = await rpc("tools/list");
    expect(body.result.tools.map((t: any) => t.name).sort()).toEqual(["dataset_licence", "find_datasets"]);
  });
  it("notification gets 202", async () => {
    const res = await worker.fetch(new Request("https://w.test/mcp", { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) }), env() as any, {} as any);
    expect(res.status).toBe(202);
  });
  it("unknown method is a JSON-RPC error", async () => {
    const { body } = await rpc("nope");
    expect(body.error.code).toBe(-32601);
  });
  it("malformed body is a parse error", async () => {
    const res = await worker.fetch(new Request("https://w.test/mcp", { method: "POST", body: "{" }), env() as any, {} as any);
    expect(res.status).toBe(400);
  });
  it("GET /mcp is 405", async () => {
    const res = await worker.fetch(new Request("https://w.test/mcp"), env() as any, {} as any);
    expect(res.status).toBe(405);
  });
  it("OPTIONS /mcp is 204 with CORS; POST responses carry CORS", async () => {
    const res = await worker.fetch(new Request("https://w.test/mcp", { method: "OPTIONS" }), env() as any, {} as any);
    expect(res.status).toBe(204);
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
    expect(res.headers.get("access-control-allow-methods")).toBe("POST, GET, OPTIONS");
    expect(res.headers.get("access-control-allow-headers")).toBe("content-type, accept, mcp-protocol-version, mcp-session-id");
    expect(res.headers.get("access-control-expose-headers")).toBe("mcp-session-id");
    const { res: r2 } = await rpc("tools/list");
    expect(r2.headers.get("access-control-allow-origin")).toBe("*");
    const g = await worker.fetch(new Request("https://w.test/mcp"), env() as any, {} as any);
    expect(g.headers.get("access-control-allow-origin")).toBe("*");
  });
  it("/stats has no CORS", async () => {
    const res = await worker.fetch(new Request("https://w.test/stats"), env() as any, {} as any);
    expect(res.headers.get("access-control-allow-origin")).toBeNull();
  });
  it("resources/list and prompts/list are empty", async () => {
    expect((await rpc("resources/list")).body.result).toEqual({ resources: [] });
    expect((await rpc("prompts/list")).body.result).toEqual({ prompts: [] });
  });
  it("protocol version echo and fallback", async () => {
    expect((await rpc("initialize", { protocolVersion: "2025-11-25" })).body.result.protocolVersion).toBe("2025-11-25");
    expect((await rpc("initialize", { protocolVersion: "1999-01-01" })).body.result.protocolVersion).toBe("2025-11-25");
  });
  it("counting uses ctx.waitUntil when present", async () => {
    const pending: Promise<unknown>[] = [];
    const req = new Request("https://w.test/mcp", { method: "POST", headers: { "CF-Connecting-IP": "9.9.9.9" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "find_datasets", arguments: {} } }) });
    await worker.fetch(req, env() as any, { waitUntil: (p: Promise<unknown>) => pending.push(p) } as any);
    expect(pending).toHaveLength(1);
    await Promise.all(pending);
    expect(kv.puts).toBe(1);
  });
  it("GET / is a plain-text pointer", async () => {
    const res = await worker.fetch(new Request("https://w.test/"), env() as any, {} as any);
    const t = await res.text();
    expect(t).toContain(SITE);
    expect(t).toContain("/mcp");
  });
});

describe("dataset_licence", () => {
  it("known id returns facts and disclaimer", async () => {
    const { body } = await call("dataset_licence", { id: "m-a-p/FineFineWeb" });
    expect(body.result.isError).toBeFalsy();
    const text = body.result.content[0].text;
    expect(text).toContain(DISCLAIMER);
    expect(text).toContain("FineFineWeb");
    expect(fetched).toContain(SITE + "/data/d/m-a-p/FineFineWeb.json");
  });
  it("unknown id is a tool error", async () => {
    const { res, body } = await call("dataset_licence", { id: "no/such" });
    expect(res.status).toBe(200);
    expect(body.result.isError).toBe(true);
  });
  it("invalid id is rejected without fetching", async () => {
    for (const id of ["../etc/passwd", "../index", "..", "./x", "a/..", "...", "a/b/c", "a b", "", "a?x=1", 5]) {
      const { body } = await call("dataset_licence", { id });
      expect(body.result.isError).toBe(true);
    }
    expect(fetched.filter((u) => u.includes("/data/d/"))).toEqual([]);
  });
  it("200 non-JSON facts response is treated as unknown id", async () => {
    globalThis.fetch = (async () => new Response("<html>fallback</html>", { status: 200 })) as any;
    const { body } = await call("dataset_licence", { id: "a/b" });
    expect(body.result.isError).toBe(true);
    expect(body.result.content[0].text).toContain("Could not read the published facts for dataset 'a/b'");
  });
  it("unknown tool is a tool error", async () => {
    const { body } = await call("zzz", {});
    expect(body.result.isError).toBe(true);
  });
});

describe("find_datasets", () => {
  const ids = async (args: any) => {
    const { body } = await call("find_datasets", args);
    return JSON.parse(body.result.content[0].text).datasets.map((d: any) => d.id);
  };
  it("no filters sorted by downloads", async () => {
    expect((await ids({}))[0]).toBe("huggingface/documentation-images");
  });
  it("filters by signal", async () => {
    expect(await ids({ signal: "permissive" })).toEqual(["hallucinations-leaderboard/results", "m-a-p/FineFineWeb"]);
  });
  it("filters by task", async () => {
    expect(await ids({ task: "text-generation" })).toEqual(["m-a-p/FineFineWeb", "a/conflicted", "ownerless"]);
  });
  it("filters by licence, case-insensitive", async () => {
    expect(await ids({ licence: "Apache-2.0" })).toEqual(["hallucinations-leaderboard/results", "m-a-p/FineFineWeb"]);
  });
  it("combines filters", async () => {
    expect(await ids({ signal: "permissive", task: "text-generation" })).toEqual(["m-a-p/FineFineWeb"]);
  });
  it("limit applies and is capped at 50", async () => {
    expect(await ids({ limit: 2 })).toHaveLength(2);
    const big = Array.from({ length: 80 }, (_, i) => [`o/d${i}`, "permissive", "mit", [], 1000 - i]);
    globalThis.fetch = (async () => new Response(JSON.stringify(big))) as any;
    _resetCache();
    expect(await ids({ limit: 500 })).toHaveLength(50);
  });
  it("coerces a numeric-string limit and tolerates rows without tasks", async () => {
    expect(await ids({ limit: "2" })).toHaveLength(2);
    globalThis.fetch = (async () => new Response(JSON.stringify([["o/x", "permissive", "mit", null, 5]]))) as any;
    _resetCache();
    expect(await ids({ task: "t" })).toEqual([]);
    expect((await call("find_datasets", { limit: "abc" })).body.result.isError).toBe(true);
  });
  it("rejects a bad signal as a tool error", async () => {
    const { body } = await call("find_datasets", { signal: "bogus" });
    expect(body.result.isError).toBe(true);
  });
  it("includes the disclaimer", async () => {
    const { body } = await call("find_datasets", {});
    expect(JSON.parse(body.result.content[0].text).disclaimer).toBe(DISCLAIMER);
  });
  it("caches index.json", async () => {
    await call("find_datasets", {});
    await call("find_datasets", {});
    expect(fetched.filter((u) => u.endsWith("/data/index.json"))).toHaveLength(1);
  });
});

describe("counting and stats", () => {
  it("same client same week counts once", async () => {
    await call("dataset_licence", { id: "m-a-p/FineFineWeb" });
    await call("find_datasets", {});
    await call("find_datasets", {});
    expect(kv.puts).toBe(1);
    const [k, v] = [...kv.store.entries()][0];
    expect(k).toMatch(/^w:\d{4}-W\d{2}:[0-9a-f]{16}$/);
    expect(k).not.toContain("1.2.3.4");
    expect(v.value).toBe("1");
    expect(v.ttl).toBe(70 * 86400);
  });
  it("different clients count separately", async () => {
    await call("find_datasets", {}, "1.1.1.1");
    await call("find_datasets", {}, "2.2.2.2");
    expect(kv.store.size).toBe(2);
  });
  it("only tools/call counts", async () => {
    await rpc("initialize");
    await rpc("tools/list");
    expect(kv.puts).toBe(0);
  });
  it("/stats is 401 without the secret", async () => {
    const res = await worker.fetch(new Request("https://w.test/stats"), env() as any, {} as any);
    expect(res.status).toBe(401);
    const bad = await worker.fetch(new Request("https://w.test/stats", { headers: { authorization: "Bearer x" } }), env() as any, {} as any);
    expect(bad.status).toBe(401);
  });
  it("/stats returns weekly counts", async () => {
    kv.store.set("w:2026-W45:aaaa", { value: "1" });
    kv.store.set("w:2026-W45:bbbb", { value: "1" });
    kv.store.set("w:2026-W46:cccc", { value: "1" });
    kv.store.set("w:2026-W46:dddd", { value: "1" });
    kv.store.set("w:2026-W46:eeee", { value: "1" });
    const res = await worker.fetch(new Request("https://w.test/stats", { headers: { authorization: "Bearer s3cret" } }), env() as any, {} as any);
    expect(await res.json()).toEqual({ weeks: { "2026-W45": 2, "2026-W46": 3 } });
    expect(kv.lists).toBeGreaterThan(1);
  });
});
