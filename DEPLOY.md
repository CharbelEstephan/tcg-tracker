# Deploying tcg-tracker

The app is a small Flask server (`app:app`) backed by a Neon Postgres database,
hosted on **Render**.

## Runtime

- **Start command** (set in the Render dashboard, since there's no `Procfile`):
  ```
  gunicorn app:app
  ```
- **Dependencies:** `requirements.txt` (Flask, psycopg2-binary, python-dotenv, gunicorn).

## Environment variables

Set these in the Render dashboard (and in a local `.env`, which is gitignored):

| Var | What it is |
|---|---|
| `DATABASE_URL` | Neon connection string (`postgresql://…?sslmode=require`) |
| `APP_PASSWORD` | The shared password you log in with |
| `SECRET_KEY`   | Long random string used to sign session cookies |

The whole app is behind a login gate (`@app.before_request`), except `/login`,
`/static`, and `/ping`.

## Keeping the free tier awake during a game

Render's free tier spins the service down after **~15 minutes** with no inbound
traffic. A Riftbound game runs longer than that, so without help the service is
asleep by the time you submit and the cold start feels like a timeout.

Two layers guard against this:

### 1. In-app heartbeat (already built in)

While a log-game page is open, the browser pings `/ping` every **5 minutes**
(see the heartbeat script in `templates/index.html` and `templates/pokemon.html`).
`/ping` is a trivial public endpoint — no DB, no template — so it's cheap.

Limit: it only works while that browser tab is open and awake. Close the tab or
let the device sleep and the service can still spin down.

### 2. UptimeRobot (optional, keeps it awake 24/7)

For a tab-independent guarantee, add an external monitor:

1. Find your Render URL (e.g. `https://<your-service>.onrender.com`); the ping
   target is that **+ `/ping`**.
2. At [uptimerobot.com](https://uptimerobot.com), **Add New Monitor**:
   - **Type:** HTTP(s)
   - **Name:** `tcg-tracker keep-alive`
   - **URL:** your `…/ping` URL
   - **Interval:** 5 minutes (free-plan minimum; well under the 15-min window)
3. Save. It hits `/ping` every 5 minutes so the service never idles out.

**Tradeoff:** Render's free tier allows **750 instance-hours/month**. Keeping the
service always-on uses ~730 of them — fine for this one service, but adding a
second free Render service would push past 750 and they'd start sleeping. If
that happens, drop UptimeRobot (the in-app heartbeat still covers active games)
or move to Render's paid Starter plan, which removes spin-down entirely.

## Database migrations

Schema changes are plain SQL files (`migrate_*.sql`, `seed_*.sql`) run once
against the Neon database — e.g. via the Neon SQL editor or a one-off script.
They're written to be idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).
