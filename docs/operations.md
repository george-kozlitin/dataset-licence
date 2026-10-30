# Operations

## Layout

- `dlf/` - the generator. `python -m dlf build` writes the static site to `site/`.
- `mcp/` - the MCP Worker (Cloudflare Workers, KV namespace `STATS`).
- `.github/workflows/build.yml` - weekly build and deploy (Monday 05:17 UTC, or by hand).
- `.github/workflows/test.yml` - tests and lint on every push.

## Run a build by hand

Locally (no deploy):

    python -m venv .venv && .venv/bin/pip install -e ".[dev]"
    .venv/bin/python -m dlf build --limit 50000 --out site --cache facts-cache \
        --base-url https://YOUR-DOMAIN --contact YOU@EXAMPLE

Exit code 0 is success. Exit code 2 means the failure budget was exceeded or the
dataset list was empty; nothing should be deployed then.

Hugging Face rate limits (anonymous, per IP, fixed 5-minute windows: 500 API
requests, 3000 resolver requests, 100 page requests) shape the generator; no
token is used. The list call does not return file listings (`siblings`) for
datasets any more, so every dataset that is fetched costs one tree call in the
API bucket, plus its README and licence files through `/resolve/` (resolver
bucket) and source datasets that are not in the listing through `/api/`. The
API bucket is the bound: about 1.6 datasets per second, roughly 6,000 per hour.
The client reads the `RateLimit` header: on a 429 it waits `t` seconds plus one
(then `Retry-After`, then exponential backoff), never more than 330 s at once,
and when a successful response reports `r=0` it waits out `t` before the next
request to that bucket. Pacing is 5 requests per second overall.

### 50,000 datasets: cache and deadline

- `--cache DIR` reads the facts of the previous build (the `data/f/*.json`
  shards). A dataset whose Hub revision (`sha`) is unchanged is not fetched
  again: its card-derived facts are reused, downloads and likes come from the
  listing, and sources that are in the listing get their current licence.
- `--deadline-minutes N` stops fetching after N minutes. Datasets processed
  after that keep last build's facts when there are any (stale), and datasets
  never fetched before wait for the next build (deferred). Deferred datasets
  are not failures and do not count toward the 5% budget. A fetch that fails
  for a dataset with cached facts keeps those facts (and still counts as a
  failure).
- The workflow restores the cache from the Actions cache, falls back to the
  live site's `data/f/` shards, and saves it again even when a later step fails.
- The first build of 50,000 (no cache) needs about 8-9 hours of Hub time, so
  it takes two runs with the default 285-minute deadline; datasets are
  processed most downloaded first. After that a weekly run fetches only the
  datasets that changed.

### Pages layout (free plan: 20,000 files per deployment)

- The 10,000 most downloaded datasets have static pages (`d/<id>/index.html`).
- The other pages are packed into `p/<xxx>.json` (4096 shards); facts of every
  dataset are in `data/f/<xx>.json` (256 shards). Shard keys: FNV-1a of the
  lower-case ID (`dlf/shards.py`).
- `_worker.js` (advanced mode) runs only for `/d/*` and `/data/d/*`
  (`_routes.json`). It serves the static file when there is one, and otherwise
  the page or facts from the shard: 308 to the canonical address for a missing
  slash or other letter case, 404 with the not-found page for unknown IDs.
- `data/index.json` (used by `find_datasets`) holds the 5,000 most downloaded;
  `data/ids.json` (site search) holds every ID.
- The build fails (exit 2) if the site has more than 19,500 files.
- Beyond the 5,000 most downloaded, pages where nothing about the licence was found (no declared licence, no
  licence name, no licence file, no sources) carry `noindex` and are left out
  of the sitemap.

### Live check

The MCP Worker answers `GET /v1/check?id=<id>` and `dataset_licence` for
datasets outside the index by reading the Hub live (`mcp/src/check.ts`, a port
of the Python rules pinned by `tests/fixtures/parity.json`; regenerate it with
`python -m tests.parity`). Results are cached for 6 hours; the `CHECK_LIMIT`
binding allows 20 checks per minute per IP. The site uses it from the search
box and from the not-found page of `/d/<id>/`.

In GitHub: Actions -> build -> Run workflow. Every successful run deploys both
the site and the MCP Worker (the Worker deploy is idempotent).

Manual deploy of an already built `site/`:

    cd mcp && npm ci
    CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... \
        npx wrangler pages deploy ../site --project-name dataset-licence --branch main
    npx wrangler deploy        # the Worker

## First-time setup

1. **Pages project.** Create it once, with `main` as the production branch:
   `cd mcp && npx wrangler pages project create dataset-licence --production-branch main`
   (or in the dashboard: Workers & Pages -> Create -> Pages -> Direct Upload).
   The production branch must be `main`; the workflow deploys with `--branch main`,
   and any other branch name would publish only a preview.
   Cloudflare Pages serves `404.html` with status 404 for unknown paths.
2. **Custom domain.** Pages project -> Custom domains -> add the domain, and let
   Cloudflare create the DNS record. Put the same URL (no trailing slash) in the
   repository variable `BASE_URL` and in `SITE_URL` in `mcp/wrangler.toml`.
3. **KV namespace.** `cd mcp && npx wrangler kv namespace create STATS`. Copy the
   printed `id` into the `[[kv_namespaces]]` block of `mcp/wrangler.toml`
   (replacing the all-zero placeholder) and commit it. Also replace
   `example.invalid` in `SITE_URL`. The workflow refuses to deploy the Worker
   while either placeholder is still there.
4. **Worker secrets**, set once by hand (the workflow does not touch them):

       cd mcp
       npx wrangler secret put MCP_SALT           # long random string, hashes client ids
       npx wrangler secret put MCP_STATS_SECRET   # bearer token for /stats

   Generate each with `openssl rand -hex 32`. Changing `MCP_SALT` makes every
   client count as new that week, so avoid rotating it mid-test.
5. **First Worker deploy.** `cd mcp && npx wrangler deploy`, then attach a route
   or use the `workers.dev` URL, and make sure `SITE_URL` matches the site.
6. **Cloudflare API token** (dashboard -> My Profile -> API Tokens): permissions
   Account / Cloudflare Pages / Edit, Account / Workers Scripts / Edit and
   Account / Workers KV Storage / Edit, limited to this account. Note the account id from the dashboard sidebar.
7. **GitHub repository settings** (Settings -> Secrets and variables -> Actions):
   - Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `TG_TOKEN`, `TG_CHAT_ID`
     (the Telegram bot token used for failure alerts).
   - Variables: `BASE_URL` (required), `CONTACT_EMAIL` (optional, shown on the
     About page), `PAGES_PROJECT` (optional, defaults to `dataset-licence`).
8. Run the `build` workflow once by hand.

A failed or cancelled run posts "dataset-licence build failed: <run URL>" to
the Telegram chat in `TG_CHAT_ID`. If `TG_TOKEN` is empty or rejected, the alert step
itself fails with a clear message ("alert not sent: TG_TOKEN empty" or the
Telegram error body), so the run shows red in the Actions tab.

## Reading MCP stats

`/stats` on the Worker returns JSON, counts of distinct hashed clients per ISO
week, and needs the bearer secret:

    curl -s -H "Authorization: Bearer $MCP_STATS_SECRET" https://WORKER-HOST/stats

Output: `{"weeks": {"2026-W50": 12, ...}}`. Weekly keys expire after a TTL, so
copy the numbers into your notes each week. Never paste the secret into an
issue, a commit or a chat.

## Rotating tokens

- **Cloudflare API token:** create a new token with the same three permissions,
  update the `CLOUDFLARE_API_TOKEN` secret in GitHub, run the build workflow by
  hand to confirm, then delete the old token in the dashboard.
- **Telegram token:** revoke and reissue it in BotFather, update `TG_TOKEN`.
- **MCP_STATS_SECRET:** `npx wrangler secret put MCP_STATS_SECRET` again; the
  old value stops working at once.
- **MCP_SALT:** see the warning in first-time setup step 4.

## Stopping the refresh

To stop the refresh, disable the `build` workflow (Actions -> build -> ...
-> Disable workflow). The last deployed site stays up.
