import { afterEach, beforeEach, describe, expect, it } from "vitest";
import worker, { _resetCache } from "../src/index";
import { checkDataset, MAX_SOURCES } from "../src/check";

const SHA = "ec5f1f46e9af79639a90684a7a70a956c4998f04";
const HFD = "https://huggingface.co/datasets/";
const realFetch = globalThis.fetch;
let routes: Record<string, () => Response>;
let fetched: string[];

const json = (v: unknown, status = 200) => () => new Response(JSON.stringify(v), { status });
const text = (v: string) => () => new Response(v);

beforeEach(() => {
  _resetCache();
  fetched = [];
  routes = {
    "https://huggingface.co/api/datasets/acme/data": json({
      id: "acme/data", sha: SHA, tags: ["license:apache-2.0"], gated: "manual", downloads: 12, likes: 3,
      lastModified: "2026-10-01T00:00:00.000Z",
      cardData: { source_datasets: ["src/sa", "ghost/gone", "original"], task_categories: ["translation"] },
    }),
    [`https://huggingface.co/api/datasets/acme/data/tree/${SHA}`]: json([
      { type: "directory", path: "data" }, { type: "file", path: "LICENSE.txt" }, { type: "file", path: "LICENSE" }, { type: "file", path: "README.md" },
    ]),
    [`${HFD}acme/data/resolve/${SHA}/README.md`]: text("---\nlicense: apache-2.0\n---\n# Citation\n```bibtex\n@misc{x}\n```\n"),
    [`${HFD}acme/data/resolve/${SHA}/LICENSE.txt`]: text("Custom terms"),
    [`${HFD}acme/data/resolve/${SHA}/LICENSE`]: text("Apache License\nVersion 2.0, January 2004"),
    "https://huggingface.co/api/datasets/src/sa": json({ id: "src/sa", sha: "b".repeat(40), tags: ["license:cc-by-sa-4.0"] }),
  };
  globalThis.fetch = (async (input: any) => {
    const url = String(typeof input === "string" ? input : input.url);
    fetched.push(url);
    const route = routes[url.split("?")[0]];
    return route ? route() : new Response("not found", { status: 404 });
  }) as any;
});
afterEach(() => { globalThis.fetch = realFetch; });

describe("checkDataset", () => {
  it("builds facts with evidence pinned to the revision", async () => {
    const f: any = await checkDataset("acme/data");
    expect(f.signal).toBe("conflict");
    expect(f.reason).toBe("The declared licence (apache-2.0) may conflict with a source dataset's reported share-alike licence (cc-by-sa-4.0).");
    expect(f.stated_licence).toBe("apache-2.0");
    expect(f.stated_from).toBe("tag");
    expect(f.stated_evidence).toBe(`${HFD}acme/data/blob/${SHA}/README.md`);
    expect(f.licence_files).toEqual(["LICENSE.txt", "LICENSE"]);
    expect(f.file_licence).toBe("apache-2.0");
    expect(f.file_evidence).toBe(`${HFD}acme/data/blob/${SHA}/LICENSE`);
    expect(f.sources).toEqual([
      { id: "src/sa", licence: "cc-by-sa-4.0", found: true, evidence: `${HFD}src/sa/blob/${"b".repeat(40)}/README.md` },
      { id: "ghost/gone", licence: null, found: false, evidence: `${HFD}ghost/gone` },
    ]);
    expect(f.gated).toBe("manual");
    expect(f.tasks).toEqual(["translation"]);
    expect(f.citation).toBe("@misc{x}");
    expect(f.revision).toBe(SHA);
    expect(f.sources_omitted).toBe(0);
  });
  it("returns null for a missing or private dataset", async () => {
    expect(await checkDataset("no/such")).toBeNull();
  });
  it("links the file list and the API record when there is no card or licence file", async () => {
    routes["https://huggingface.co/api/datasets/bare/set"] = json({ id: "bare/set", sha: SHA, tags: ["license:mit"] });
    routes[`https://huggingface.co/api/datasets/bare/set/tree/${SHA}`] = json([]);
    const f: any = await checkDataset("bare/set");
    expect(f.signal).toBe("permissive");
    expect(f.stated_evidence).toBe(`https://huggingface.co/api/datasets/bare/set/revision/${SHA}`);
    expect(f.file_evidence).toBe(`${HFD}bare/set/tree/${SHA}`);
  });
  it("caps the source lookups", async () => {
    const many = Array.from({ length: MAX_SOURCES + 5 }, (_, i) => `s/${i}`);
    routes["https://huggingface.co/api/datasets/big/set"] = json({ id: "big/set", sha: SHA, tags: ["license:mit"], cardData: { source_datasets: many } });
    const f: any = await checkDataset("big/set");
    expect(f.sources).toHaveLength(MAX_SOURCES);
    expect(f.sources_omitted).toBe(5);
    expect(fetched.filter((u) => u.includes("/api/datasets/s/"))).toHaveLength(MAX_SOURCES);
  });
});

const env = (limit?: boolean) => ({
  SITE_URL: "https://site.test", STATS: { get: async () => null, put: async () => {} }, MCP_SALT: "s", MCP_STATS_SECRET: "x",
  ...(limit === undefined ? {} : { CHECK_LIMIT: { limit: async () => ({ success: limit }) } }),
});
const get = (q: string, e: any = env()) => worker.fetch(new Request(`https://w.test/v1/check?${q}`), e as any, {} as any);
const tool = async (args: any) => {
  const req = new Request("https://w.test/mcp", { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "dataset_licence", arguments: args } }) });
  return (await (await worker.fetch(req, env() as any, {} as any)).json() as any).result;
};

describe("GET /v1/check", () => {
  it("answers with live facts and CORS", async () => {
    const res = await get("id=acme/data");
    expect(res.status).toBe(200);
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
    const body: any = await res.json();
    expect(body.source).toBe("live check");
    expect(body.facts.signal).toBe("conflict");
    expect(typeof body.checked_at).toBe("string");
    expect(body.disclaimer).toContain("Not legal advice");
  });
  it("rejects invalid ids without calling the Hub", async () => {
    for (const q of ["id=../x", "id=a/b/c", "", "id=a%20b"]) expect((await get(q)).status).toBe(400);
    expect(fetched).toEqual([]);
  });
  it("404 for an unknown dataset, 429 when limited, 502 when the Hub fails", async () => {
    expect((await get("id=no/such")).status).toBe(404);
    expect((await get("id=acme/data", env(false))).status).toBe(429);
    routes["https://huggingface.co/api/datasets/acme/data"] = () => new Response("down", { status: 503 });
    expect((await get("id=acme/data")).status).toBe(502);
  });
});

describe("dataset_licence live fallback", () => {
  it("checks the Hub when the dataset is not in the index", async () => {
    const r = await tool({ id: "acme/data" });
    expect(r.isError).toBeUndefined();
    const body = JSON.parse(r.content[0].text);
    expect(body.source).toBe("live check");
    expect(body.facts.stated_evidence).toBe(`${HFD}acme/data/blob/${SHA}/README.md`);
    expect(fetched[0]).toBe("https://site.test/data/d/acme/data.json");
  });
  it("live: true skips the index; a bad live flag is an error", async () => {
    await tool({ id: "acme/data", live: true });
    expect(fetched.some((u) => u.startsWith("https://site.test/"))).toBe(false);
    expect((await tool({ id: "acme/data", live: "yes" })).isError).toBe(true);
  });
  it("unknown on the Hub is a tool error", async () => {
    const r = await tool({ id: "no/such" });
    expect(r.isError).toBe(true);
    expect(r.content[0].text).toContain("was not found on the Hugging Face Hub");
  });
});
