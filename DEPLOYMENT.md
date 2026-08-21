# Deploying Prism

A genuinely-free deployment: gateway + console on Render, Postgres on Neon.
Roughly 20 minutes, no credit card required for Neon, and Render's free tier
doesn't require one for web services either.

## Why this stack

| Piece | Host | Free tier reality |
|---|---|---|
| Postgres | [Neon](https://neon.com) | Permanent free tier — 0.5 GB storage, 100 compute-hours/month, scales to zero after 5 min idle |
| Gateway (API) | [Render](https://render.com) | 750 instance-hours/month (enough for one always-on service), Docker native. Spins down after 15 min idle; next request takes ~1 min to wake |
| Console (static) | Render | Same free tier, served by nginx |

**Not Fly.io**, despite what earlier planning assumed: Fly removed its free
allowance for new accounts in October 2024. New accounts get a trial (2 VM
hours or 7 days), then pay-as-you-go. Fly is still a good choice if you're
willing to spend ~$5/month, and nothing here is Render-specific — the same
image and env vars deploy anywhere.

**Not Render's own Postgres**: its free database is deleted after 30 days,
which would take the deployment down without warning. Neon's free tier has no
such expiry.

---

## Before you start: the secrets you must not lose

Prism holds two pieces of key material that **cannot be regenerated without
data loss**:

- `CREDENTIAL_ENCRYPTION_KEY` encrypts every tenant's BYOK provider API key.
  Lose it and every stored credential becomes permanently unreadable
  ciphertext — every tenant has to re-enter their provider key.
- `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` sign every session token. Lose them and
  everyone is logged out (recoverable, but disruptive on every deploy).

Locally these are generated into `backend/keys/` and kept alive by a Docker
volume. **Managed platforms have ephemeral filesystems** — that directory is
wiped on every deploy. So in production they must come from environment
secrets instead. The gateway refuses to start with `ENVIRONMENT=production`
unless all three are set, specifically so this can't be discovered the hard
way.

Generate them once:

```bash
cd backend && python -m app.keygen
```

Keep the output somewhere safe (a password manager). Generating a *second*
time gives you different keys — which is the data-loss scenario above.

---

## 1. Database (Neon)

1. Sign up at [neon.com](https://neon.com) and create a project.
2. Copy the connection string from the dashboard. It looks like:
   `postgresql://user:pass@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`
3. **Convert it for asyncpg** — Prism uses the async driver, and asyncpg
   rejects libpq's `sslmode` parameter:

   ```
   postgresql+asyncpg://user:pass@ep-xxx.region.aws.neon.tech/neondb
   ```

   Change the scheme to `postgresql+asyncpg://` and **drop `?sslmode=require`**.
   Neon requires TLS and asyncpg negotiates it automatically, so the
   connection is still encrypted — that parameter is libpq's spelling, and
   leaving it in fails at connect time with
   `TypeError: connect() got an unexpected keyword argument 'sslmode'`
   (verified, not guessed).

Save that as your `DATABASE_URL`.

**Pooled vs direct endpoint** — Neon offers both, and the copy button often
gives you the pooled one (its hostname contains `-pooler`). Either works:
the gateway detects a transaction-mode pooler from the hostname and switches
asyncpg to unique, uncached prepared statements, because PgBouncer hands
consecutive statements to different backend connections and asyncpg would
otherwise fail with `prepared statement "__asyncpg_stmt_N__" does not exist`.
The direct endpoint (same host without `-pooler`) is marginally faster for a
single instance since it keeps a normal client-side connection pool. Use
whichever you copied.

## 2. Gateway (Render)

1. Push this repo to GitHub if you haven't.
2. In Render: **New → Blueprint**, point it at the repo. It reads
   [`render.yaml`](render.yaml) and creates both services.
3. Fill in the secrets it prompts for on `prism-gateway`:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the converted Neon URL from step 1 |
   | `ADMIN_BOOTSTRAP_PASSWORD` | a real password you choose |
   | `CREDENTIAL_ENCRYPTION_KEY` | from `python -m app.keygen` |
   | `JWT_PRIVATE_KEY` | from `python -m app.keygen` |
   | `JWT_PUBLIC_KEY` | from `python -m app.keygen` |
   | `CORS_ALLOW_ORIGINS` | leave blank for now — set in step 4 |

   The PEM values are multi-line. Render's dashboard accepts real newlines;
   if yours mangles them, the `\n`-escaped single-line form from `keygen`
   also works (the loader normalizes both).

4. Deploy. Migrations run automatically on startup. When it's live,
   `https://prism-gateway-xxxx.onrender.com/ready` should return
   `{"status":"ready","database":"ok"}`.

If it refuses to start, read the logs — the startup guard names exactly which
secret is missing. A log line like:

```
RuntimeError: Refusing to start with ENVIRONMENT=production and insecure defaults:
  - CREDENTIAL_ENCRYPTION_KEY is not set. ...
```

is the guard doing its job, not a bug: it stops the deploy *before* tenants
store credentials that a later redeploy would render unreadable. Set the named
secret and redeploy.

## 3. Console (Render)

The blueprint also creates `prism-console`. Set one variable:

| Variable | Value |
|---|---|
| `VITE_API_BASE_URL` | your gateway's URL, e.g. `https://prism-gateway-xxxx.onrender.com` |

Vite inlines this **at build time**, so changing it later requires a redeploy,
not just a restart.

## 4. Connect them

Go back to `prism-gateway` and set `CORS_ALLOW_ORIGINS` to the console's
origin, e.g. `https://prism-console-xxxx.onrender.com` (no trailing slash).
Without this the browser blocks every API call and the console looks broken
while the API is perfectly healthy.

Redeploy the gateway for it to take effect.

## 5. Verify

```bash
GATEWAY=https://prism-gateway-xxxx.onrender.com

curl -s $GATEWAY/health          # {"status":"ok"}
curl -s $GATEWAY/ready           # {"status":"ready","database":"ok"}
curl -s $GATEWAY/auth/config     # signup_enabled + the ceilings
```

Then in the console: sign up, create a key, add a BYOK provider credential,
and send a request. `TESTING.md` has the full procedure — §5b in particular
covers reconciling Prism's numbers against your provider's own console.

**Confirm the credential key is really persistent** — this is the one that
bites silently. Add a BYOK credential, trigger a redeploy in Render, then
confirm the credential still works afterward. If it doesn't,
`CREDENTIAL_ENCRYPTION_KEY` isn't actually set and the gateway fell back to
generating one.

---

## Free-tier behavior worth knowing

- **First request after idle is slow.** Render spins the service down after 15
  minutes of inactivity; waking takes ~1 minute, and Neon adds a few seconds
  waking from its own scale-to-zero. Not a bug. A cron pinging `/health` every
  10 minutes avoids it, but burns your 750 instance-hours faster.
- **Neon's 100 compute-hours/month** is the real ceiling, not storage. It only
  accrues while the database is awake, so a demo instance idles cheaply. The
  gateway sets `pool_pre_ping` so the first request after Neon suspends
  reconnects transparently instead of erroring.
- **Streaming works** — Render doesn't buffer SSE responses.

## Upgrading later

Nothing above is Render-specific: it's a standard Docker image reading env
vars, with `/ready` for health checks. Moving to Fly, Cloud Run, or a VPS is
the same image plus the same variables. Set `RUN_MIGRATIONS_ON_STARTUP=false`
and run `alembic upgrade head` as a release step if you'd rather migrations
not run on boot — the in-process runner is already safe across replicas via a
Postgres advisory lock, so this is a preference, not a requirement.

## Rotating secrets

- **Admin password**: `ADMIN_BOOTSTRAP_PASSWORD` only seeds the account on the
  *first* startup against an empty `admin_users` table. Changing it later does
  nothing — update the row directly, or drop it and let it re-seed.
- **JWT keys**: safe to rotate. Everyone is logged out; nothing is lost.
- **Credential encryption key**: **not** safe to rotate. Every stored BYOK
  credential is encrypted with the current key and there is no re-encryption
  path yet. Rotating means asking every tenant to re-enter their provider key.
