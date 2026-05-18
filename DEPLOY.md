# Deploying Bart to Fly.io

You only run `flyctl launch` once per app. Every change after that is just `flyctl deploy`.

## One-time setup

```sh
# Install the Fly CLI (Mac)
brew install flyctl

# Sign in / create your Fly account
flyctl auth login
```

## Launching the app (first time only)

From the repo root (`/Users/loctran/Downloads/BartWebsite`):

```sh
flyctl launch --copy-config --no-deploy
```

What this does:
- Reads the existing `fly.toml` (don't let it overwrite — pick "No" if asked).
- Asks for an **app name** → becomes `<name>.fly.dev`. Globally unique, like a domain.
- Asks for a **region** → pick the one closest to you (e.g. `iad` = Virginia, `sjc` = San Jose).
- Reserves the name, creates the app, sets up the free HTTPS cert.
- `--no-deploy` means "don't push the first build yet" — you need to create the volume first.

## Create the persistent volume

Bart stores SQLite (`bart.db`), uploaded materials, generated packets, and per-user Claude credentials on disk. You need a Fly volume so those survive redeploys.

```sh
flyctl volumes create bart_data --region <same-region-you-picked> --size 3
```

- `bart_data` — name must match the `[mounts]` block in `fly.toml`.
- `--size 3` — 3 GB. About $1/month. You can grow it later with `flyctl volumes extend`.

## Deploy

```sh
flyctl deploy
```

This builds the Docker image (uses your `Dockerfile`), pushes it to Fly, boots the VM, and waits for `/api/health` to return 200.

## Open it in the browser

```sh
flyctl open
```

Opens `https://<your-app>.fly.dev/` — the splash page.

## After it's up

1. Click **sign up** on the splash, create an account on your live site.
2. Once you're inside, go to **settings** → **connect claude account**.
3. Open the device-flow URL on your laptop, sign in to claude.ai, paste the code back. Bart now has your Claude credentials inside the Fly container.
4. Drop materials in. Run.

## Day-to-day operations

```sh
flyctl deploy           # ship a new version (after you edit code)
flyctl logs             # tail server logs in real time
flyctl ssh console      # shell into the running container
flyctl status           # show app state, machines, regions
flyctl scale memory 2048   # bump RAM to 2 GB if runs are slow
flyctl secrets set FOO=bar # set an env var, restarts the app
```

## Environment variables

Set these with `flyctl secrets set KEY=value` (the app restarts on change).

**Billing (Stripe).**
- `STRIPE_SECRET_KEY` — your Stripe secret key.
- `STRIPE_WEBHOOK_SECRET` — signing secret for the `/api/stripe/webhook` endpoint.
- `STRIPE_PRICE_MONTHLY` — the price id of the $10/month plan.
- `APP_PUBLIC_URL` — public site URL (default `https://studywithbart.com`); used in Stripe redirect URLs and creator referral links.

**Creator program email.** Applications are emailed to `bartcompanyai@gmail.com`. Configure an SMTP account so the mail actually sends — without it, applications are still saved and shown in the admin panel, just not emailed.
- `SMTP_USER` — sending account, e.g. `bartcompanyai@gmail.com`.
- `SMTP_PASS` — an app password for that account.
- `SMTP_HOST` / `SMTP_PORT` — default `smtp.gmail.com` / `587` (STARTTLS).
- `BART_CREATOR_COMMISSION_RATE` — creator's share of each verified payment (default `0.30`).

Approve or reject applications at `/admin-creators` (admin account only).

**Other.**
- `GOOGLE_CLIENT_ID` — enables Google sign-in (optional).
- `BART_CLAUDE_RUNS_PER_MONTH` — premium-run allowance per subscriber (default `12`).
- `BART_MAX_CONCURRENT_RUNS` — server-wide concurrent run cap (default `6`).

## Costs (rough)

- **VM**: shared-cpu-1x with 1 GB RAM, auto-sleep when idle → **free** in most months (under the free tier limit).
- **Volume**: 3 GB → about **$0.45/month**.
- **Bandwidth**: 160 GB outbound free. After that, $0.02/GB.

Expect under $1/month while you're prototyping. Add a credit card to your Fly account once you're past the free tier.

## Troubleshooting

**Build fails** → run `flyctl logs` during a deploy. Common cause: a Python package needs a system library not in `python:3.11-slim`. Add `apt-get install -y <lib>` to the Dockerfile.

**App boots but every page 404s** → check `flyctl logs`. If you see `Permission denied` on `/data`, the volume wasn't created — see "Create the persistent volume" above.

**Claude /login never completes** → SSH in with `flyctl ssh console` and check `which claude`. If missing, the Docker image didn't install it — look at the `npm install -g @anthropic-ai/claude-code` step in the Dockerfile.

**Session cookie not sticking on HTTPS** → already handled. The cookie's `secure=True` flag is set automatically from `request.url.scheme`, and uvicorn is started with `--proxy-headers` in `entrypoint.sh` so it sees the real protocol behind Fly's load balancer.

## Going to production

When you start letting actual users in:

1. **Custom domain**: `flyctl certs create studywithbart.com` (or whatever), then point a CNAME at `<app>.fly.dev`.
2. **Backups**: `flyctl volumes snapshots create bart_data` — keep at least one weekly.
3. **Bigger VM**: `flyctl scale vm shared-cpu-2x --memory 2048` once you're getting concurrent runs.
4. **Move off SQLite**: when you outgrow one machine, swap `bart.db` for Postgres (`flyctl postgres create`).
