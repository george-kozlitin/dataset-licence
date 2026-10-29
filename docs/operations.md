# Operations

## Layout

- `dlf/` - the generator. `python -m dlf build` writes the static site to `site/`.
- `mcp/` - the MCP Worker (Cloudflare Workers, KV namespace `STATS`).
- `.github/workflows/build.yml` - weekly build and deploy (Monday 05:17 UTC, or by hand).
- `.github/workflows/test.yml` - tests and lint on every push.

## Run a build by hand

Locally (no deploy):

    python -m venv .venv && .venv/bin/pip install -e ".[dev]"
    .venv/bin/python -m dlf build --limit 5000 --out site \
        --base-url https://YOUR-DOMAIN --contact YOU@EXAMPLE

Exit code 0 is success. Exit code 2 means the failure budget was exceeded or the
dataset list was empty; nothing should be deployed then.

Hugging Face rate limits (anonymous, per IP, fixed 5-minute windows: 500 API
requests, 3000 resolver requests, 100 page requests) shape the generator; no
token is used. The list call uses `full=true`, so each entry carries its file
listing (`siblings`) and no per-dataset tree call is made (the tree endpoint is
only a fallback when `siblings` is absent). README and LICENSE files are fetched
through `/resolve/main/` (resolver bucket), source datasets through `/api/`.
The client reads the `RateLimit` header: on a 429 it waits `t` seconds plus one
(then `Retry-After`, then exponential backoff), never more than 330 s at once,
and when a successful response reports `r=0` it waits out `t` before the next
request to that bucket. Pacing is 5 requests per second overall. Expect a full
5,000-dataset run to take roughly 30-60 minutes (see the live-check note in the
commit history for the measured rate); the job timeout is 120 minutes.

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
