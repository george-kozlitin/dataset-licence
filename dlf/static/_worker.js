// Pages worker: serves dataset pages and facts that are not static files from the shards
// the generator writes (p/<xxx>.json, data/f/<xx>.json). Shard keys match dlf/shards.py.
// _routes.json limits it to /d/* and /data/d/*; everything else is served as static files.

const ID = "([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)";
const PAGE_RE = new RegExp(`^/d/${ID}(/?)$`);
const FACTS_RE = new RegExp(`^/data/d/${ID}\\.json$`);

export function fnv1a(text) {
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(text)) h = Math.imul(h ^ b, 0x01000193) >>> 0;
  return h >>> 0;
}
export const pageShard = (id) => (fnv1a(id.toLowerCase()) % 4096).toString(16).padStart(3, "0");
export const factsShard = (id) => (fnv1a(id.toLowerCase()) % 256).toString(16).padStart(2, "0");

const validId = (id) => id.split("/").every((p) => p !== "." && p !== "..");

async function shard(env, url, path) {
  const res = await env.ASSETS.fetch(new Request(new URL(path, url)));
  return res.ok ? res.json() : null;
}

async function notFound(env, url) {
  const res = await env.ASSETS.fetch(new Request(new URL("/404.html", url)));
  return new Response(res.ok ? res.body : "Not found", {
    status: 404,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

export default {
  async fetch(request, env) {
    const res = await env.ASSETS.fetch(request);
    if (res.status !== 404 || request.method !== "GET" && request.method !== "HEAD") return res;
    const url = new URL(request.url);
    let path;
    try { path = decodeURIComponent(url.pathname); } catch { return notFound(env, url); }

    const page = PAGE_RE.exec(path);
    if (page && validId(page[1])) {
      const id = page[1];
      const entry = (await shard(env, url, `/p/${pageShard(id)}.json`))?.[id.toLowerCase()];
      if (!entry) return notFound(env, url);
      const [canonical, html] = entry;
      if (canonical !== id || !page[2]) {
        const to = "/d/" + canonical.split("/").map(encodeURIComponent).join("/") + "/" + url.search;
        return new Response(null, { status: 308, headers: { location: to } });
      }
      return new Response(request.method === "HEAD" ? null : html, {
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=0, must-revalidate" },
      });
    }

    const facts = FACTS_RE.exec(path);
    if (facts && validId(facts[1])) {
      const f = (await shard(env, url, `/data/f/${factsShard(facts[1])}.json`))?.[facts[1].toLowerCase()];
      if (f && f.id === facts[1]) {
        return new Response(request.method === "HEAD" ? null : JSON.stringify(f, null, 1), {
          headers: { "content-type": "application/json", "access-control-allow-origin": "*" },
        });
      }
    }
    return notFound(env, url);
  },
};
