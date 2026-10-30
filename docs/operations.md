# Operations

## Layout

- `dlf/` - the generator. `python -m dlf build` writes the static site to `site/`.
- `mcp/` - the MCP Worker (Cloudflare Workers, KV namespace `STATS`).
- `.github/workflows/build.yml` - daily build and deploy (04:23 UTC, or by hand); a run that leaves
  datasets for later starts the next run itself (see below).
- `.github/workflows/test.yml` - tests and lint on every push.

## Run a build by hand

Locally (no deploy):

    python -m venv .venv && .venv/bin/pip install -e ".[dev]"
    .venv/bin/python -m dlf build --limit 0 --out site --cache facts-cache --cache-out facts-cache \
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

### Every public dataset: cache, deadline, chained runs

- `--limit 0` lists every public dataset (about 1.1 million on 2026-10-30), most
  downloaded first. The listing is read page by page; heavy fields (card data,
  description, citation) are kept only for datasets that will be fetched.
- `--cache DIR` reads the facts of the previous build (`*.txt` line shards, or
  the earlier `*.json` shards). A dataset whose Hub revision (`sha`) is
  unchanged is not fetched again: its card-derived facts are reused, downloads
  and likes come from the listing, and sources that are in the listing get their
  current licence.
- `--cache-out DIR` writes the facts of every dataset right after fetching,
  before the site is rendered and before the failure budget is checked, so a
  failed render never loses a run's fetching.
- `--deadline-minutes N` stops fetching N minutes after the process started
  (listing time included). Datasets processed after that keep the last facts
  when there are any (stale); datasets never fetched before wait (deferred).
  Deferred datasets are not failures. A fetch that fails for a dataset with
  cached facts keeps those facts (and still counts as a failure).
- The workflow restores the cache from the Actions cache (key per run, restore
  by prefix), falls back to the live site's `data/f/` shards, and saves it again
  even when a later step fails. Runs never overlap (`concurrency: build`).
- Filling: one run fetches about 28,000 datasets in its 285 minutes. After a
  successful deploy with `deferred > 0` and `fetched > 0` the run dispatches the
  next one, so the catalogue fills by itself (about 38 runs from 50,000 to
  1.1 million). Once nothing is deferred, the daily run only fetches new and
  changed datasets; removed datasets drop out because they are not listed.

### Pages layout (free plan: 20,000 files per deployment)

- The 10,000 most downloaded datasets have static pages (`d/<id>/index.html`).
- The other pages are lines in `p/<xxx>.txt` (4096 shards, `<lower id>\t[id, html]`);
  facts of every dataset are lines in `data/f/<xxx>.txt` (1024 shards). Shard
  keys: FNV-1a of the lower-case ID (`dlf/shards.py`). At 1.1 million a page
  shard is about 1.7 MB; the worker finds one line with `indexOf` and parses
  only that line.
- `_worker.js` (advanced mode) runs only for `/d/*` and `/data/d/*`
  (`_routes.json`). It serves the static file when there is one, and otherwise
  the page or facts from the shard: 308 to the canonical address for a missing
  slash or other letter case, 404 with the not-found page for unknown IDs.
  `/data/d/<id>.json` answers in any letter case with the canonical `id`.
- `data/index.json` (used by `find_datasets`) holds the 5,000 most downloaded;
  `data/ids.json` (site search) holds the 50,000 most downloaded. A full
  `owner/name` typed in search is looked up through `/data/d/<id>.json`.
- Listing pages (licence, signal, task) list only indexable datasets, at most
  10 pages of 200 each; the total count still covers every dataset.
- The build checks the planned file count before rendering and fails (exit 2)
  above 19,500 files.
- Beyond the 5,000 most downloaded, pages where nothing about the licence was
  found (no declared licence, no licence name, no licence file, no sources)
  carry `noindex` and are left out of the sitemap.

### Live check

The MCP Worker answers `GET /v1/check?id=<id>` and `dataset_licence` for
datasets not in the build by reading the Hub live (`mcp/src/check.ts`, a port
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
