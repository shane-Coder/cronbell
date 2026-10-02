# Cronbell

*Formerly PulseCheck — renamed in October 2026.*

A dead-man's-switch monitoring service for cron jobs, scheduled scripts, and background tasks.

Your scheduled job pings a unique URL every time it finishes successfully. If a ping doesn't
show up within the expected window, Cronbell assumes something broke and emails you —
before you find out the hard way, days later.

> **Status:** the architecture change is done — the always-on Celery worker is gone, replaced
> by a GitHub Actions cron (see [Stack](#stack)). The live demo URL currently serves a
> maintenance page while I finish the UI rebuild; everything below runs fully locally via
> Docker in the meantime.

## Why

Most monitoring (Prometheus, UptimeRobot, etc.) watches things that are already running.
Nothing watches for a cron job that never started, or a script that crashed before finishing —
that's the gap this fills. Cronbell watches for the *absence* of a signal, not the presence
of an error.

## Features

- Ping-based monitoring: one `curl` call at the end of any job (cron, Docker, Kubernetes
  CronJob, CI step, systemd timer — anything that can make an HTTP request)
- Per-monitor expected interval + grace period, with a three-tier status (up / late / down) so
  a warning shows up before an alert email does
- Uptime timeline and uptime % per monitor, computed from real status-transition history
- Prometheus-compatible `/metrics` endpoint, scoped per account with a token, for pinning
  monitors onto an existing Grafana dashboard
- Slack, Discord, and generic webhook alerts alongside email, with a one-click test send for each
- Opt-in public status pages — one shareable link per account, per-monitor toggle, name/status/uptime only
- Admin panel (env-var-gated, paginated) to see and manage accounts
- Self-service account settings: change password, alert channels, delete your own account
- Forgot-password email reset flow, and email verification with a resend option
- Rate limiting on every state-changing endpoint (auth, monitor CRUD, pings), CSRF protection
  on every authenticated form, inactivity reminders + auto-delete for abandoned accounts
- Light/dark theme, public landing page + docs — no login wall on the marketing pages

## Stack

- **API/backend:** FastAPI
- **DB:** PostgreSQL (SQLAlchemy ORM)
- **Scheduled checks:** no background worker — the overdue-monitor sweep and daily inactivity
  check are plain functions behind an internal, token-guarded HTTP endpoint
  (`POST /internal/run-overdue-check`), triggered by a GitHub Actions cron in this repo
  instead of a process that has to stay running 24/7
- **Redis:** rate limiting only (`slowapi`) — it used to also be the Celery broker; that's gone
- **Frontend:** server-rendered Jinja2 templates (no separate JS build), IBM Plex Sans/Mono
- **Auth:** email + password, JWT stored in an HttpOnly cookie
- **Alerts:** SMTP email, plus Slack, Discord, and generic webhooks (each with a test send)

## Local development

```bash
cp .env.example .env
docker compose up --build
```

Then visit http://localhost:8000, register an account, and create a monitor. Each monitor
gets a ping URL like:

```
http://localhost:8000/ping/<token>
```

Point a cron job at it, e.g.:

```bash
* * * * * /path/to/your/script.sh && curl -fsS http://localhost:8000/ping/<token>
```

If a ping doesn't arrive within `period + grace` seconds, the monitor is marked down and an
alert email goes out — but only once something actually triggers the check, since there's no
background process doing that on its own locally. Trigger it by hand while developing:

```bash
curl -X POST localhost:8000/internal/run-overdue-check -H "X-Internal-Token: $INTERNAL_CRON_TOKEN"
```

(`INTERNAL_CRON_TOKEN` is in your `.env`.) In production this same endpoint gets called every
few minutes by [`.github/workflows/run-overdue-check.yml`](.github/workflows/run-overdue-check.yml).

See `/docs` on a running instance for integration examples (Docker, Kubernetes, GitHub
Actions, systemd, Airflow) and how this fits next to Prometheus/Grafana.

`.env.example` has every setting, including `ADMIN_EMAILS` (comma-separated emails that get
`/admin` access) and the inactivity-cleanup thresholds. Without real SMTP credentials, emails
are logged instead of sent — fine for local dev, but you'll want a real provider (Brevo,
SendGrid, etc.) for anything beyond that.

### Running tests

No Docker or Postgres needed for these — they run against an in-memory SQLite DB and a
`memory://` rate-limit store, so they're fast and fully isolated from anything else running
locally:

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
pytest
```

Runs automatically on every push via [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Project layout

```
backend/
  app/
    main.py              FastAPI app + route registration
    config.py             Settings via env vars
    database.py            SQLAlchemy engine/session
    templating.py            Shared Jinja2 environment + template globals
    models.py                  User, Monitor, PingEvent, StatusEvent
    security.py                  Password hashing + JWT
    deps.py                        Auth dependencies (current user, admin gate)
    rate_limit.py                   Redis-backed rate limiting (slowapi)
    timeline.py                      Uptime-timeline computation from StatusEvent history
    tasks.py                          Overdue sweep + inactivity reminders/deletion (plain functions)
    email_utils.py                     SMTP sending helper
    alert_utils.py                      Slack/Discord/generic webhook senders
    maintenance.py                       Static "under maintenance" page (see MAINTENANCE_MODE)
    routers/
      auth.py                              register/login/logout
      account.py                            change password, alert channels, delete account
      admin.py                              account list + delete (env-gated)
      monitors.py                           dashboard, monitor CRUD + edit, uptime timeline
      ping.py                                the actual ping-receiving endpoint
      internal.py                           the two endpoints the GitHub Actions cron calls
      metrics.py                            per-account Prometheus scrape endpoint
      status_page.py                        the public, no-auth status page
      pages.py                              /docs
    templates/                              Jinja2 HTML (landing, dashboard, docs, admin, ...)
    static/                                  CSS
  tests/                                   pytest suite — auth, monitors, account, admin, alert-firing
  pytest.ini
  requirements-dev.txt                    pytest + httpx, not in the production image
```

## Roadmap

- [x] v1: monitors, email alerts, dashboard
- [x] v2: uptime % history, Prometheus metrics, admin panel, account self-service
- [x] v3: UI rebuild, input hardening, DB indexing, drop the always-on worker for a
      GitHub Actions cron
- [x] v4: Slack/Discord/generic webhook alerts, opt-in public status pages
- [ ] v5: "start"/"fail" ping variants, pricing, payments, team accounts, an API

## Deployment

Deployed on Fly.io (`fly.toml` in `backend/`) — an auto-stop-when-idle web process and a small
Postgres instance. No dedicated worker machine anymore: v2 ran one 24/7 just to fire two
scheduled checks a minute apart, which turned out to be the single biggest line item on the
Fly bill. v3 replaced it with a GitHub Actions cron hitting an internal endpoint, cutting the
always-on compute to just Postgres. Nothing Fly-specific in the code itself;
`docker-compose.yml` maps directly onto whatever Docker-based host you'd rather use (Railway,
Render, your own box).

## License

MIT — see [LICENSE](LICENSE).

## Notes

Built and maintained by [Shivam](https://github.com/shane-Coder), with Claude Code used as a
development tool for scaffolding and debugging.
