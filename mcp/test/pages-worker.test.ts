import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
// @ts-expect-error plain JS module shipped with the site
import pagesWorker, { factsShard, findLine, fnv1a, pageShard } from "../../dlf/static/_worker.js";

const P = JSON.parse(readFileSync(fileURLToPath(new URL("../../tests/fixtures/parity.json", import.meta.url)), "utf8"));
const SITE = "https://site.test";
const PAGE = "<!doctype html><h1>o/Deep</h1>";

function assets(files: Record<string, string>) {
  const seen: string[] = [];
  return {
    seen,
    ASSETS: {
      fetch: async (req: Request) => {
        const path = new URL(req.url).pathname;
        seen.push(path);
        if (path === "/d/static/one" ) return new Response(null, { status: 308, headers: { location: "/d/static/one/" } });
        return path in files ? new Response(files[path]) : new Response("nope", { status: 404 });
      },
    },
  };
}
// One shard line as dlf/shards.py writes it.
const line = (id: string, value: unknown) => `${id.toLowerCase()}\t${JSON.stringify(value)}\n`;
// The ID under test sits between other lines, so the lookup has to find it inside the text.
const shard = (id: string, value: unknown) => line("a/before", ["a/Before", "x"]) + line(id, value) + line("z/after", ["z/After", "y"]);
const site = () => assets({
  "/d/static/one/": "<h1>static</h1>",
  "/404.html": "<h1>Page not found</h1>",
  [`/p/${pageShard("o/Deep")}.txt`]: shard("o/Deep", ["o/Deep", PAGE]),
  [`/data/f/${factsShard("o/Deep")}.txt`]: shard("o/Deep", { id: "o/Deep", signal: "permissive" }),
});
const get = (env: any, path: string, method = "GET") => pagesWorker.fetch(new Request(SITE + path, { method }), env);

describe("shard keys match dlf/shards.py", () => {
  it("fnv1a, facts and page shards", () => {
    for (const [id, h, f, p] of P.shards) {
      expect(fnv1a(id)).toBe(h);
      expect(factsShard(id)).toBe(f);
      expect(pageShard(id)).toBe(p);
    }
  });
});

describe("shard lines", () => {
  const text = line("o/first", ["o/First", "1"]) + line("o/deep", ["o/Deep", "tab\there\nline"]) + line("o/last", ["o/Last", "3"]);
  it("finds the first, a middle and the last line", () => {
    expect(findLine(text, "o/first")).toEqual(["o/First", "1"]);
    expect(findLine(text, "o/deep")).toEqual(["o/Deep", "tab\there\nline"]);
    expect(findLine(text, "o/last")).toEqual(["o/Last", "3"]);
    expect(findLine(text.slice(0, -1), "o/last")).toEqual(["o/Last", "3"]); // no final newline
  });
  it("matches whole IDs only", () => {
    for (const key of ["o/dee", "deep", "o/deep/x", "o", "", "missing/id"]) expect(findLine(text, key), key).toBeNull();
    expect(findLine("", "o/deep")).toBeNull();
    expect(findLine("o/deep\t{broken\n", "o/deep")).toBeNull();
  });
  it("facts shards are 3 hex digits below 1024, page shards below 4096", () => {
    for (const id of ["a", "squad", "google/xtreme", "o/Deep", "x/y"]) {
      expect(factsShard(id)).toMatch(/^[0-9a-f]{3}$/);
      expect(parseInt(factsShard(id), 16)).toBeLessThan(1024);
      expect(parseInt(pageShard(id), 16)).toBeLessThan(4096);
      expect(factsShard(id)).toBe(factsShard(id.toUpperCase()));
    }
  });
});

describe("Pages worker", () => {
  it("passes static files and their redirects through", async () => {
    const env = site();
    expect(await (await get(env, "/d/static/one/")).text()).toBe("<h1>static</h1>");
    expect((await get(env, "/d/static/one")).status).toBe(308);
    expect(env.seen).not.toContain(`/p/${pageShard("static/one")}.txt`);
  });
  it("serves a page and facts from the first line of a shard", async () => {
    const env = assets({
      [`/p/${pageShard("o/Deep")}.txt`]: line("o/deep", ["o/Deep", PAGE]),
      [`/data/f/${factsShard("o/Deep")}.txt`]: line("o/deep", { id: "o/Deep" }),
    });
    expect(await (await get(env, "/d/o/Deep/")).text()).toBe(PAGE);
    expect(await (await get(env, "/data/d/o/Deep.json")).json()).toEqual({ id: "o/Deep" });
  });
  it("answers 404 when the shard file is missing", async () => {
    const env = assets({ "/404.html": "<h1>Page not found</h1>" });
    for (const path of ["/d/o/Deep/", "/data/d/o/Deep.json"]) expect((await get(env, path)).status, path).toBe(404);
  });
  it("serves a page from its shard", async () => {
    const res = await get(site(), "/d/o/Deep/");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/html; charset=utf-8");
    expect(await res.text()).toBe(PAGE);
  });
  it("redirects a missing slash and other letter case to the canonical address", async () => {
    for (const [path, to] of [["/d/o/Deep", "/d/o/Deep/"], ["/d/O/deep/", "/d/o/Deep/"], ["/d/o/deep?x=1", "/d/o/Deep/?x=1"]]) {
      const res = await get(site(), path);
      expect(res.status, path).toBe(308);
      expect(res.headers.get("location"), path).toBe(to);
    }
  });
  it("answers 404 with the not-found page for unknown or invalid IDs", async () => {
    for (const path of ["/d/o/none/", "/d/../x/", "/d/a/b/c/", "/d/%E0%A4%A/", "/data/d/o/none.json"]) {
      const res = await get(site(), path);
      expect(res.status, path).toBe(404);
      expect(await res.text(), path).toContain("Page not found");
    }
  });
  it("serves facts JSON in any letter case, with the canonical id", async () => {
    const res = await get(site(), "/data/d/o/Deep.json");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ id: "o/Deep", signal: "permissive" });
    expect(await (await get(site(), "/data/d/o/deep.json")).json()).toEqual({ id: "o/Deep", signal: "permissive" });
    expect((await get(site(), "/data/d/o/none.json")).status).toBe(404);
  });
  it("HEAD has no body", async () => {
    const res = await get(site(), "/d/o/Deep/", "HEAD");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("");
  });
});
