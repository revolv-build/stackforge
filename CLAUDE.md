# Project: Baseline Admin

> Central admin hub for managing all baseline-forked apps on this server.
> Server stats, app registry, health monitoring, log viewer, service controls.

**Live:** https://admin.revolv.uk
**Repo:** https://github.com/revolv-build/baselineadmin
**Server:** 165.22.123.55 (1 CPU, 1.9GB RAM, 67GB disk)

---

## Core Architecture

| Layer | Technology |
|---|---|
| **Framework** | Flask 3.x (Python 3.12) |
| **Database** | SQLite with WAL mode |
| **Server** | Gunicorn behind Nginx |
| **Port** | 5002 |
| **Process manager** | systemd |

### What This App Does
- **Dashboard** — server overview (CPU, memory, disk, uptime, load) + app grid with live health status
- **App Registry** — register apps with URL, GitHub, server path, port, systemd service, log path
- **Health Checks** — pings each app's URL (or checks systemd) on every dashboard load
- **Log Viewer** — tail and filter gunicorn access logs for any app, with request stats
- **Service Controls** — restart systemd services from the browser (admin only)
- **API** — `/api/health`, `/api/apps`, `/api/server` for programmatic access

### Apps on This Server

| App | Port | Domain | Service | Log |
|-----|------|--------|---------|-----|
| Community | 5001 | community.revolv.uk | community.service | /var/log/community.log |
| Brandkit | 5000 | brandkit.revolv.uk | brandkit.service | /var/log/brandkit.log |
| Ganttly | - | Not deployed | - | - |
| Baseline Admin | 5002 | admin.revolv.uk | baselineadmin.service | /var/log/baselineadmin.log |

---

## Database Schema

```
users — Platform accounts (auth, admin, account management)
apps  — Registered apps (name, slug, url, github_url, server_path, port, service_name, log_path, description, status, last_check)
```

---

## Key Routes

### Hub
- `/dashboard` — server overview + app grid
- `/apps/new` — register a new app
- `/apps/<slug>` — app detail (config, stats, service info, recent logs)
- `/apps/<slug>/edit` — edit app config
- `/apps/<slug>/logs` — full log viewer with filtering
- `/apps/<slug>/restart` — restart systemd service (admin only)
- `/apps/<slug>/delete` — remove from dashboard

### API
- `/api/health` — hub health check (no auth)
- `/api/apps` — JSON list of apps with status
- `/api/server` — JSON server stats

### Auth (from baseline)
- `/login`, `/register`, `/logout`
- `/forgot-password`, `/reset-password/<token>`
- `/verify-email/<token>`

### Account (from baseline)
- `/account` — profile, password, avatar, GDPR export/delete

### Admin (from baseline)
- `/admin` — user management, impersonation

---

## How To

### Register a new app
1. Go to `/apps/new` or click "+ Register App"
2. Fill in name, URL, GitHub, server path, port, service name, log path
3. The app will show on the dashboard with live health checks

### Deploy a new baseline app
1. Fork `revolv-build/baseline`
2. Set up on server with `bash setup.sh appname domain.com PORT`
3. Register it in baselineadmin

### Check why an app is down
1. Open the app detail page — check the status badge
2. Look at Service Details (state, PID, memory)
3. Check recent logs at the bottom
4. Use "View Full Logs" to search for errors
5. Use "Restart Service" if needed

---

## Patterns & Conventions

Same as baseline (see baseline's CLAUDE.md) plus:

- **Server utilities** in `get_server_stats()`, `check_app_health()`, `get_service_status()`, `read_log_lines()`, `get_log_stats()`
- **App CRUD** replaces the notes CRUD from baseline
- **Templates** in `templates/apps/` (new, view, edit, logs)
- **Hub CSS** at the bottom of `style.css` (stat-grid, app-grid, log-viewer, etc.)

---

## Do Not Touch

- **Other apps' services** — this app can restart them, but be careful
- **Log files** — read-only access, never write to other apps' logs
- **Database files** — never delete `data/app.db`

---

## Key Decisions Log

**2026-04-06** — Created from baseline. Replaced notes CRUD with app registry + server monitoring hub.

---

## Session Protocol

At the end of every session:
1. Update **Current State** below
2. Add a dated entry to **Session Log**
3. Commit: `docs: session log YYYY-MM-DD`

---

## Current State

### Built and working
- Server overview dashboard with live stats
- App registry with CRUD
- Health checks (HTTP + systemd)
- Log viewer with filtering
- Service restart (admin only)
- API endpoints
- Auth, account, admin (from baseline)
- Pre-seeded with 4 apps

### Not yet done
- [ ] Deploy to server (systemd, nginx, SSL)
- [ ] Auto-refresh dashboard
- [ ] Screenshot capture per app
- [ ] Uptime history
- [ ] Email alerts on downtime

---

## Session Log

### 2026-04-06
**Initial build from baseline.**

Forked baseline, replaced notes with server admin hub. Added server stats, app registry, health checks, log viewer, service restart, API endpoints, hub CSS, seed with actual server apps.
