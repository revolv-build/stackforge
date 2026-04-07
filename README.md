# StackForge

Managed Flask hosting platform with Claude Code built in.

**Domain:** stackforge.co.uk

---

## What is StackForge?

StackForge gives developers a managed server with one-click Flask app deployment and an integrated Claude Code AI terminal — all from the browser. No SSH, no DevOps, just build.

### Core Features (inherited from baselineadmin)
- Server dashboard with live stats (CPU, memory, disk, load, connections)
- App registry with health monitoring and log viewer
- One-click app deployment from baseline template
- Web-based Claude Code terminal per app (persistent sessions)
- Mobile terminal access
- Reference image uploads for Claude Code prompts
- Service management (restart, status, logs)
- Auth, admin, account management
- Dark theme, responsive, mobile burger menu

---

## Product Vision

Turn the internal admin tool into a paid SaaS platform where customers can:

1. **Sign up** and create a workspace
2. **Provision a server** (DigitalOcean droplet) with one click
3. **Deploy Flask apps** from templates or their own repos
4. **Use Claude Code** directly in the browser to build and manage their apps
5. **Scale up** by upgrading server tier or adding app slots

### Target Pricing

| Tier | Server | Apps Included | Claude Code Sessions | Price |
|------|--------|---------------|---------------------|-------|
| Starter | 1 vCPU / 2GB | 2 | 1 concurrent | ~£15/mo |
| Pro | 2 vCPU / 4GB | 5 | 2 concurrent | ~£35/mo |
| Scale | 4 vCPU / 8GB | 12 | 4 concurrent | ~£75/mo |

---

## Roadmap

### Phase 1: Foundation
- [x] Server overview dashboard
- [x] App registry with CRUD
- [x] Health checks (HTTP + systemd)
- [x] Log viewer with filtering
- [x] Service restart controls
- [x] One-click app deployment pipeline
- [x] Claude Code web terminal (ttyd)
- [x] Persistent terminal sessions
- [x] Mobile terminal access
- [x] Reference image uploads
- [x] Security hardening (path validation, session limits, token auth, concurrent limits)
- [x] Responsive mobile UI with burger menu

### Phase 2: Multi-tenancy
- [ ] Workspace model (org/team)
- [ ] User roles (owner, admin, member, viewer)
- [ ] Workspace-scoped apps and servers
- [ ] Invitation system
- [ ] Workspace settings and branding

### Phase 3: Billing
- [ ] Stripe integration (subscriptions + usage-based add-ons)
- [ ] Server tier selection and upgrade/downgrade
- [ ] App slot add-on purchasing
- [ ] Usage metering (compute hours, storage, bandwidth)
- [ ] Billing dashboard and invoices

### Phase 4: Server Provisioning
- [ ] DigitalOcean API integration
- [ ] Automated droplet creation per workspace
- [ ] Server setup automation (baseline template, nginx, SSL, systemd)
- [ ] Server resize/destroy
- [ ] Region selection
- [ ] Automated DNS configuration

### Phase 5: Enhanced Developer Experience
- [ ] GitHub integration (deploy on push, branch previews)
- [ ] Custom domain support per app
- [ ] Environment variable management UI
- [ ] Database management (backup, restore, migrate)
- [ ] Prompt templates for Claude Code (saved per app)
- [ ] Session history and activity timeline

### Phase 6: Monitoring and Reliability
- [ ] Resource monitoring graphs (sparklines for CPU, memory over time)
- [ ] Uptime history per app
- [ ] Email/Slack alerts on downtime
- [ ] Auto-restart on crash
- [ ] Automated daily backups
- [ ] App screenshot thumbnails on dashboard

### Phase 7: Scale
- [ ] Multi-server management from single dashboard
- [ ] Load balancing support
- [ ] PostgreSQL option (beyond SQLite)
- [ ] Team collaboration features
- [ ] API for programmatic management
- [ ] CLI tool for power users

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Flask 3.x (Python 3.12) |
| Database | SQLite (WAL) → PostgreSQL for SaaS |
| Server | Gunicorn behind Nginx |
| Terminal | ttyd (WebSocket) |
| AI | Claude Code |
| Billing | Stripe |
| Infrastructure | DigitalOcean |
| CI/CD | GitHub Actions |
| SSL | Let's Encrypt |

---

## Development

```bash
cd /root/stackforge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # configure SECRET_KEY
make run
```

---

## License

Proprietary. All rights reserved.
