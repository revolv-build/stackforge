"""
Baseline Admin
──────────────
Central admin hub for managing all baseline-forked apps on this server.
Server stats, app registry, health monitoring, log viewer, quick links.

Architecture:
  - Auth: login, register, password reset, email verification
  - Account: profile, password, avatar, GDPR export/delete
  - Hub: app registry, server overview, health checks, log viewer
  - Admin: user management, impersonation, platform stats
"""

import json
import os
import re
import signal
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
import secrets as secrets_mod
import shutil

from flask import (
    Flask, Response, render_template, request,
    redirect, url_for, session, flash, g, abort, send_from_directory, jsonify
)
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from markupsafe import escape, Markup
import markdown as md_lib
import bleach
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────

load_dotenv()

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "data" / "app.db"

UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "csv", "zip",
                      "png", "jpg", "jpeg", "gif", "svg", "mp4", "mov", "webm"}
MAX_UPLOAD_MB = 50

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 7  # 7 days
app.config["WTF_CSRF_TIME_LIMIT"] = 3600  # 1 hour CSRF token validity
DEFAULT_PORT = int(os.environ.get("PORT", 5002))

# App metadata
APP_NAME = os.environ.get("APP_NAME", "Baseline Admin")
APP_SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@example.com")

# Refuse to boot with default secret key in production
if os.environ.get("FLASK_ENV") == "production" and app.secret_key == "change-me-in-production":
    raise RuntimeError("SECRET_KEY must be set in production.")

# CSRF protection
csrf = CSRFProtect(app)

# Rate limiting
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per minute"],
                  storage_uri="memory://")

# Security headers
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

(APP_DIR / "data").mkdir(exist_ok=True)

# ── Markdown Rendering ────────────────────────────────────────────

ALLOWED_TAGS = [
    "p", "br", "strong", "em", "a", "ul", "ol", "li", "code", "pre",
    "blockquote", "h1", "h2", "h3", "h4", "img", "hr", "del", "table",
    "thead", "tbody", "tr", "th", "td",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title"],
}

def render_markdown(text):
    if not text:
        return ""
    raw_html = md_lib.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
    clean = bleach.clean(raw_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)
    clean = clean.replace("<a ", '<a target="_blank" rel="noopener" ')
    return Markup(clean)

@app.template_filter("markdown")
def markdown_filter(text):
    return render_markdown(text)

def strip_markdown(text):
    if not text:
        return ""
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    t = re.sub(r'\*(.+?)\*', r'\1', t)
    t = re.sub(r'__(.+?)__', r'\1', t)
    t = re.sub(r'_(.+?)_', r'\1', t)
    t = re.sub(r'#{1,6}\s*', '', t)
    t = re.sub(r'^\s*[-*+]\s+', '', t, flags=re.MULTILINE)
    t = re.sub(r'^\s*\d+\.\s+', '', t, flags=re.MULTILINE)
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
    t = re.sub(r'`{1,3}[^`]*`{1,3}', '', t)
    t = re.sub(r'^>\s*', '', t, flags=re.MULTILINE)
    t = re.sub(r'---+', '', t)
    t = re.sub(r'\n{2,}', ' ', t)
    return t.strip()

@app.template_filter("timeago")
def timeago_filter(dt_str):
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return dt_str[:10] if dt_str else ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = seconds // 60
        return f"{m}m ago"
    elif seconds < 86400:
        h = seconds // 3600
        return f"{h}h ago"
    elif seconds < 604800:
        d = seconds // 86400
        return f"{d}d ago"
    elif seconds < 2592000:
        w = seconds // 604800
        return f"{w}w ago"
    else:
        return dt_str[:10]

@app.template_filter("strip_markdown")
def strip_markdown_filter(text):
    return strip_markdown(text)

# ── Email (Resend) ────────────────────────────────────────────────

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", f"{APP_NAME} <noreply@example.com>")

def send_email(to, subject, html_body):
    if not RESEND_API_KEY:
        print(f"[EMAIL SKIPPED — no API key] To: {to}, Subject: {subject}")
        return False
    payload = json.dumps({
        "from": EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html_body
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.URLError as e:
        print(f"[EMAIL ERROR] {e}")
        return False

# ── Token Generation ──────────────────────────────────────────────

from itsdangerous import URLSafeTimedSerializer
_serializer = URLSafeTimedSerializer(app.secret_key)

def generate_token(data, salt="default"):
    return _serializer.dumps(data, salt=salt)

def verify_token(token, salt="default", max_age=3600):
    try:
        return _serializer.loads(token, salt=salt, max_age=max_age)
    except Exception:
        return None

# ── Database ──────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            email_verified INTEGER NOT NULL DEFAULT 0,
            bio           TEXT DEFAULT '',
            location      TEXT DEFAULT '',
            website       TEXT DEFAULT '',
            avatar_path   TEXT DEFAULT '',
            created       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS apps (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            slug          TEXT NOT NULL UNIQUE,
            url           TEXT DEFAULT '',
            github_url    TEXT DEFAULT '',
            server_path   TEXT DEFAULT '',
            port          INTEGER DEFAULT 0,
            service_name  TEXT DEFAULT '',
            log_path      TEXT DEFAULT '',
            description   TEXT DEFAULT '',
            status        TEXT DEFAULT 'unknown',
            last_check    TEXT DEFAULT '',
            created       TEXT NOT NULL,
            updated       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pending_deploys (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            slug          TEXT NOT NULL UNIQUE,
            config        TEXT NOT NULL,
            created       TEXT NOT NULL
        );
    """)

    run_migrations(db)

    # Create default platform admin if no users exist
    row = db.execute("SELECT COUNT(*) FROM users").fetchone()
    if row[0] == 0:
        db.execute(
            "INSERT INTO users (name, email, password_hash, is_admin, email_verified, created) VALUES (?, ?, ?, ?, ?, ?)",
            ("Admin", "admin@example.com", generate_password_hash("changeme"), 1, 1, datetime.now(timezone.utc).isoformat())
        )
        print(f"Default admin created — email: admin@example.com  password: changeme")

    # Check if workspace_id column exists on apps table
    cols = [row[1] for row in db.execute("PRAGMA table_info(apps)").fetchall()]
    if 'workspace_id' not in cols:
        db.execute("ALTER TABLE apps ADD COLUMN workspace_id INTEGER REFERENCES workspaces(id)")

    # Backfill default workspace if none exist
    ws_count = db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
    if ws_count == 0:
        admin = db.execute("SELECT id FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
        if admin:
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO workspaces (name, slug, owner_id, created, updated) VALUES (?, ?, ?, ?, ?)",
                ("StackForge", "stackforge", admin[0], now, now)
            )
            ws_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role, created) VALUES (?, ?, ?, ?)",
                (ws_id, admin[0], "owner", now)
            )
            db.execute("UPDATE apps SET workspace_id = ? WHERE workspace_id IS NULL", (ws_id,))

    db.commit()
    db.close()

def run_migrations(db):
    migrations_dir = APP_DIR / "migrations"
    if not migrations_dir.exists():
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            applied TEXT NOT NULL
        )
    """)
    applied = {row[0] for row in db.execute("SELECT name FROM _migrations").fetchall()}
    migration_files = sorted(migrations_dir.glob("*.sql"))
    for f in migration_files:
        if f.name not in applied:
            print(f"[MIGRATE] Applying {f.name}")
            db.executescript(f.read_text())
            db.execute("INSERT INTO _migrations (name, applied) VALUES (?, ?)",
                       (f.name, datetime.now(timezone.utc).isoformat()))
    db.commit()

# ── Helpers ───────────────────────────────────────────────────────

def get_user_by_id(uid):
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

def get_user_by_email(email):
    return get_db().execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()

def current_user():
    uid = session.get("user_id")
    if uid:
        return get_user_by_id(uid)
    return None

def login_user(user):
    session.clear()
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session.permanent = True

def slugify(text):
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return slug[:60]

def avatar_html(user, size=22):
    if user and user["avatar_path"]:
        return Markup(f'<img src="/uploads/{user["avatar_path"]}" class="avatar-img" style="width:{size}px;height:{size}px;" />')
    name = user["name"] if user else "?"
    return Markup(f'<span class="avatar" style="width:{size}px;height:{size}px;font-size:{max(10, size//2)}px;">{name[0].upper()}</span>')

def paginate(query, params, page, per_page=20):
    db = get_db()
    count_q = f"SELECT COUNT(*) FROM ({query})"
    total = db.execute(count_q, params).fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    offset = (page - 1) * per_page
    items = db.execute(f"{query} LIMIT ? OFFSET ?", (*params, per_page, offset)).fetchall()
    return items, total, pages, page

def search_like(term):
    escaped = term.replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"

WORKSPACE_ROLES = {'owner': 3, 'member': 2, 'viewer': 1}

def get_workspace_by_slug(slug):
    return get_db().execute("SELECT * FROM workspaces WHERE slug = ?", (slug,)).fetchone()

def get_user_workspaces(user_id):
    return get_db().execute("""
        SELECT w.* FROM workspaces w
        JOIN workspace_members wm ON w.id = wm.workspace_id
        WHERE wm.user_id = ? ORDER BY w.name
    """, (user_id,)).fetchall()

@app.context_processor
def inject_globals():
    user = current_user()
    user_ws = []
    if user:
        user_ws = get_user_workspaces(user["id"])
    return dict(
        current_user=user,
        avatar_html=avatar_html,
        app_name=APP_NAME,
        support_email=APP_SUPPORT_EMAIL,
        workspace=getattr(g, 'workspace', None),
        workspace_role=getattr(g, 'workspace_role', None),
        user_workspaces=user_ws,
    )

# ── Auth Decorators ───────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

def platform_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user or not user["is_admin"]:
            flash("Admin access required.", "error")
            return redirect("/workspaces")
        return f(*args, **kwargs)
    return decorated

def workspace_required(min_role='viewer'):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login_page"))
            ws_slug = kwargs.get('ws_slug')
            if not ws_slug:
                abort(404)
            ws = get_workspace_by_slug(ws_slug)
            if not ws:
                abort(404)
            user = current_user()
            # Platform admins bypass membership checks
            if user and user["is_admin"]:
                g.workspace = ws
                g.workspace_role = 'owner'
                return f(*args, **kwargs)
            # Check membership
            db = get_db()
            membership = db.execute(
                "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
                (ws["id"], session["user_id"])
            ).fetchone()
            if not membership:
                abort(403)
            role = membership["role"]
            if WORKSPACE_ROLES.get(role, 0) < WORKSPACE_ROLES.get(min_role, 0):
                abort(403)
            g.workspace = ws
            g.workspace_role = role
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── Auth Routes ───────────────────────────────────────────────────

@app.route("/")
def landing():
    if session.get("user_id"):
        return redirect("/workspaces")
    return render_template("landing.html")

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login_page():
    if session.get("user_id"):
        return redirect("/workspaces")
    err = None
    form_ts = str(int(time.time()))
    if request.method == "POST":
        if request.form.get("website_url", ""):
            err = "Invalid email or password."
            return render_template("login.html", err=err, form_ts=form_ts)
        ts = request.form.get("_ts", "0")
        try:
            if time.time() - int(ts) < 1.5:
                err = "Invalid email or password."
                return render_template("login.html", err=err, form_ts=form_ts)
        except (ValueError, TypeError):
            pass
        user = get_user_by_email(request.form.get("email", ""))
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            login_user(user)
            return redirect("/workspaces")
        err = "Invalid email or password."
    return render_template("login.html", err=err, form_ts=form_ts)

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def register_page():
    if session.get("user_id"):
        return redirect("/workspaces")
    err = None
    name = ""
    email = ""
    form_ts = str(int(time.time()))
    if request.method == "POST":
        if request.form.get("website_url", ""):
            err = "Registration failed."
            return render_template("register.html", err=err, name="", email="", form_ts=form_ts)
        ts = request.form.get("_ts", "0")
        try:
            if time.time() - int(ts) < 2:
                err = "Please slow down and try again."
                return render_template("register.html", err=err, name="", email="", form_ts=form_ts)
        except (ValueError, TypeError):
            pass
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        if not name or not email or not password:
            err = "All fields are required."
        elif len(password) < 8:
            err = "Password must be at least 8 characters."
        elif password != password_confirm:
            err = "Passwords do not match."
        elif get_user_by_email(email):
            err = "An account with that email already exists."
        else:
            db = get_db()
            db.execute(
                "INSERT INTO users (name, email, password_hash, created) VALUES (?, ?, ?, ?)",
                (name, email, generate_password_hash(password), datetime.now(timezone.utc).isoformat())
            )
            db.commit()
            user = get_user_by_email(email)
            login_user(user)
            # Auto-create a workspace for the new user
            ws_slug = slugify(name)
            if not ws_slug:
                ws_slug = f"workspace-{user['id']}"
            # Ensure unique slug
            existing_ws = db.execute("SELECT id FROM workspaces WHERE slug = ?", (ws_slug,)).fetchone()
            if existing_ws:
                ws_slug = f"{ws_slug}-{user['id']}"
            now_ws = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO workspaces (name, slug, owner_id, created, updated) VALUES (?, ?, ?, ?, ?)",
                (f"{name}'s Workspace", ws_slug, user["id"], now_ws, now_ws)
            )
            ws_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role, created) VALUES (?, ?, ?, ?)",
                (ws_id, user["id"], "owner", now_ws)
            )
            db.commit()
            token = generate_token(user["id"], salt="email-verify")
            verify_url = request.host_url.rstrip("/") + f"/verify-email/{token}"
            send_email(
                to=email,
                subject=f"Verify your email - {APP_NAME}",
                html_body=f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
                    <h2 style="color:#333;">Welcome to {APP_NAME}!</h2>
                    <p style="color:#666;">Hi {name},</p>
                    <p style="color:#666;">Please verify your email address to get full access.</p>
                    <p style="margin:24px 0;">
                        <a href="{verify_url}" style="background:#000;color:#fff;padding:12px 24px;border-radius:4px;text-decoration:none;font-weight:500;">Verify Email</a>
                    </p>
                </div>
                """
            )
            flash("Welcome! Check your email to verify your account.", "success")
            return redirect(f"/workspaces/{ws_slug}/dashboard")
    return render_template("register.html", err=err, name=name, email=email, form_ts=form_ts)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ── Password Reset ────────────────────────────────────────────────

@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per minute", methods=["POST"])
def forgot_password():
    sent = False
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_user_by_email(email)
        if user:
            token = generate_token(user["id"], salt="password-reset")
            reset_url = request.host_url.rstrip("/") + f"/reset-password/{token}"
            send_email(
                to=user["email"],
                subject="Reset your password",
                html_body=f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
                    <h2 style="color:#333;">Reset your password</h2>
                    <p style="color:#666;">Hi {user['name']},</p>
                    <p style="color:#666;">Click the button below to reset your password. This link expires in 1 hour.</p>
                    <p style="margin:24px 0;">
                        <a href="{reset_url}" style="background:#000;color:#fff;padding:12px 24px;border-radius:4px;text-decoration:none;font-weight:500;">Reset Password</a>
                    </p>
                    <p style="color:#999;font-size:13px;">If you didn't request this, you can safely ignore this email.</p>
                </div>
                """
            )
        sent = True
    return render_template("forgot_password.html", sent=sent)

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user_id = verify_token(token, salt="password-reset", max_age=3600)
    if not user_id:
        flash("This reset link has expired or is invalid.", "error")
        return redirect("/forgot-password")
    user = get_user_by_id(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect("/forgot-password")
    err = None
    if request.method == "POST":
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        if len(password) < 8:
            err = "Password must be at least 8 characters."
        elif password != password_confirm:
            err = "Passwords do not match."
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(password), user_id))
            db.commit()
            flash("Password updated! You can now log in.", "success")
            return redirect("/login")
    return render_template("reset_password.html", err=err, token=token)

# ── Email Verification ────────────────────────────────────────────

@app.route("/verify-email/<token>")
def verify_email(token):
    user_id = verify_token(token, salt="email-verify", max_age=86400)
    if not user_id:
        flash("This verification link has expired or is invalid.", "error")
        return redirect("/login")
    db = get_db()
    db.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
    db.commit()
    flash("Email verified!", "success")
    if session.get("user_id"):
        return redirect("/workspaces")
    return redirect("/login")

@app.route("/resend-verification", methods=["POST"])
@login_required
@limiter.limit("2 per minute")
def resend_verification():
    user = get_user_by_id(session["user_id"])
    if user and not user["email_verified"]:
        token = generate_token(user["id"], salt="email-verify")
        verify_url = request.host_url.rstrip("/") + f"/verify-email/{token}"
        send_email(
            to=user["email"],
            subject="Verify your email",
            html_body=f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
                <h2 style="color:#333;">Verify your email</h2>
                <p style="color:#666;">Hi {user['name']},</p>
                <p style="color:#666;">Click the button below to verify your email address.</p>
                <p style="margin:24px 0;">
                    <a href="{verify_url}" style="background:#000;color:#fff;padding:12px 24px;border-radius:4px;text-decoration:none;font-weight:500;">Verify Email</a>
                </p>
            </div>
            """
        )
        flash("Verification email sent! Check your inbox.", "success")
    return redirect("/workspaces")

# ══════════════════════════════════════════════════════════════════
#  SERVER UTILITIES — system info, health checks, log reading
# ══════════════════════════════════════════════════════════════════

def get_claude_code_dirs():
    """Return set of working directories where Claude Code instances are running."""
    dirs = set()
    try:
        result = subprocess.run(
            ["pgrep", "-x", "claude"], capture_output=True, text=True, timeout=5
        )
        for pid in result.stdout.strip().split("\n"):
            if not pid:
                continue
            cwd_link = f"/proc/{pid}/cwd"
            try:
                dirs.add(os.readlink(cwd_link))
            except OSError:
                continue
    except Exception:
        pass
    return dirs


# ── Terminal Sessions ─────────────────────────────────────────────

TERMINAL_CONF_DIR = Path("/etc/nginx/terminal.d")
TERMINAL_DATA_DIR = Path("/root/baselineadmin/data/terminals")
TERMINAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
TERMINAL_MAX_CONCURRENT = 2
TERMINAL_TIMEOUT_SECS = 7200  # 2 hours


def _get_terminal_session(slug):
    """Read terminal session info from file."""
    path = TERMINAL_DATA_DIR / f"{slug}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def _save_terminal_session(slug, info):
    """Save terminal session info to file."""
    info["created"] = time.time()
    (TERMINAL_DATA_DIR / f"{slug}.json").write_text(json.dumps(info))


def _remove_terminal_session(slug):
    """Remove terminal session file."""
    path = TERMINAL_DATA_DIR / f"{slug}.json"
    if path.exists():
        path.unlink()


def _kill_terminal(info):
    """Kill a ttyd process and remove its nginx conf."""
    try:
        os.kill(info["pid"], signal.SIGTERM)
    except OSError:
        pass
    conf = TERMINAL_CONF_DIR / f"{info.get('token', '')}.conf"
    if conf.exists():
        conf.unlink()


def _active_terminal_count():
    """Count currently running terminal sessions."""
    count = 0
    for f in TERMINAL_DATA_DIR.glob("*.json"):
        try:
            info = json.loads(f.read_text())
            os.kill(info["pid"], 0)
            count += 1
        except (OSError, KeyError, json.JSONDecodeError):
            pass
    return count


def _cleanup_stale_terminals():
    """Remove entries whose ttyd process has died or timed out."""
    if not TERMINAL_DATA_DIR.exists():
        return
    reloaded = False
    now = time.time()
    for f in TERMINAL_DATA_DIR.glob("*.json"):
        try:
            info = json.loads(f.read_text())
            os.kill(info["pid"], 0)
            # Kill if timed out
            if now - info.get("created", 0) > TERMINAL_TIMEOUT_SECS:
                _kill_terminal(info)
                f.unlink(missing_ok=True)
                reloaded = True
                continue
        except (OSError, KeyError, json.JSONDecodeError):
            # Process dead or bad file — clean up
            try:
                conf = TERMINAL_CONF_DIR / f"{info.get('token', '')}.conf"
                if conf.exists():
                    conf.unlink()
                    reloaded = True
            except Exception:
                pass
            f.unlink(missing_ok=True)
    if reloaded:
        subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, timeout=10)


# Clean up stale terminal configs on startup
_cleanup_stale_terminals()
for conf in TERMINAL_CONF_DIR.glob("*.conf"):
    conf.unlink()


def get_server_stats():
    """Gather live server statistics."""
    stats = {}

    # Uptime
    try:
        with open("/proc/uptime") as f:
            uptime_seconds = float(f.read().split()[0])
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        stats["uptime"] = f"{days}d {hours}h"
        stats["uptime_seconds"] = uptime_seconds
    except Exception:
        stats["uptime"] = "unknown"

    # Load average
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            stats["load_1m"] = parts[0]
            stats["load_5m"] = parts[1]
            stats["load_15m"] = parts[2]
    except Exception:
        stats["load_1m"] = stats["load_5m"] = stats["load_15m"] = "?"

    # Memory
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = int(parts[1].strip().split()[0])  # kB
                    meminfo[key] = val
            total = meminfo.get("MemTotal", 1)
            available = meminfo.get("MemAvailable", 0)
            used = total - available
            stats["mem_total_gb"] = round(total / 1048576, 1)
            stats["mem_used_gb"] = round(used / 1048576, 1)
            stats["mem_pct"] = round(used / total * 100, 1) if total else 0
    except Exception:
        stats["mem_total_gb"] = stats["mem_used_gb"] = 0
        stats["mem_pct"] = 0

    # Disk
    try:
        result = subprocess.run(["df", "-B1", "/"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            total = int(parts[1])
            used = int(parts[2])
            stats["disk_total_gb"] = round(total / 1073741824, 1)
            stats["disk_used_gb"] = round(used / 1073741824, 1)
            stats["disk_pct"] = round(used / total * 100, 1) if total else 0
    except Exception:
        stats["disk_total_gb"] = stats["disk_used_gb"] = 0
        stats["disk_pct"] = 0

    # CPU count
    try:
        stats["cpu_count"] = os.cpu_count() or 1
    except Exception:
        stats["cpu_count"] = 1

    # Hostname
    try:
        stats["hostname"] = subprocess.run(["hostname"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        stats["hostname"] = "unknown"

    # IP
    try:
        stats["ip"] = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=5
        ).stdout.strip().split()[0]
    except Exception:
        stats["ip"] = "unknown"

    # Process count
    try:
        result = subprocess.run(["ps", "aux", "--no-headers"], capture_output=True, text=True, timeout=5)
        stats["processes"] = len(result.stdout.strip().split("\n"))
    except Exception:
        stats["processes"] = "?"

    # Swap usage
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])
            swap_total = meminfo.get("SwapTotal", 0)
            swap_free = meminfo.get("SwapFree", 0)
            swap_used = swap_total - swap_free
            stats["swap_total_mb"] = round(swap_total / 1024)
            stats["swap_used_mb"] = round(swap_used / 1024)
            stats["swap_pct"] = round(swap_used / swap_total * 100, 1) if swap_total else 0
    except Exception:
        stats["swap_total_mb"] = stats["swap_used_mb"] = 0
        stats["swap_pct"] = 0

    # Network connections
    try:
        result = subprocess.run(["ss", "-tun", "state", "established"], capture_output=True, text=True, timeout=5)
        stats["net_connections"] = max(0, len(result.stdout.strip().split("\n")) - 1)
    except Exception:
        stats["net_connections"] = "?"

    # Active Claude Code sessions
    stats["claude_sessions"] = _active_terminal_count()

    # OS info
    try:
        result = subprocess.run(["lsb_release", "-d", "-s"], capture_output=True, text=True, timeout=5)
        stats["os"] = result.stdout.strip().strip('"')
    except Exception:
        stats["os"] = "Linux"

    return stats


def check_app_health(app_row):
    """Check if an app is responding. Returns 'online', 'offline', or 'unknown'."""
    url = app_row["url"]
    if not url:
        # Fall back to checking systemd service
        service = app_row["service_name"]
        if service:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", service],
                    capture_output=True, text=True, timeout=5
                )
                return "online" if result.stdout.strip() == "active" else "offline"
            except Exception:
                return "unknown"
        return "unknown"

    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return "online" if resp.status < 500 else "offline"
    except Exception:
        # Try GET if HEAD fails
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return "online" if resp.status < 500 else "offline"
        except Exception:
            return "offline"


def get_service_status(service_name):
    """Get detailed systemd service status."""
    if not service_name:
        return None
    try:
        result = subprocess.run(
            ["systemctl", "show", service_name, "--no-pager",
             "-p", "ActiveState,SubState,MainPID,MemoryCurrent,ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5
        )
        info = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                info[key] = val
        return info
    except Exception:
        return None


def read_log_lines(log_path, num_lines=100):
    """Read the last N lines from a log file."""
    if not log_path or not os.path.exists(log_path):
        return []
    try:
        result = subprocess.run(
            ["tail", "-n", str(num_lines), log_path],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except Exception:
        return []


def get_log_stats(log_path):
    """Parse access log for basic request stats."""
    if not log_path or not os.path.exists(log_path):
        return {"total_requests": 0, "today_requests": 0, "error_requests": 0, "size_bytes": 0}

    stats = {"total_requests": 0, "today_requests": 0, "error_requests": 0}
    today = datetime.now().strftime("%d/%b/%Y")

    try:
        stats["size_bytes"] = os.path.getsize(log_path)
        # Count lines efficiently
        result = subprocess.run(["wc", "-l", log_path], capture_output=True, text=True, timeout=5)
        stats["total_requests"] = int(result.stdout.strip().split()[0])

        # Today's requests
        result = subprocess.run(
            ["grep", "-c", today, log_path],
            capture_output=True, text=True, timeout=5
        )
        stats["today_requests"] = int(result.stdout.strip()) if result.returncode == 0 else 0

        # Error requests (4xx and 5xx)
        result = subprocess.run(
            ["grep", "-cE", '" [45][0-9]{2} ', log_path],
            capture_output=True, text=True, timeout=5
        )
        stats["error_requests"] = int(result.stdout.strip()) if result.returncode == 0 else 0

    except Exception:
        pass

    return stats


def format_bytes(b):
    """Format bytes to human-readable."""
    if b < 1024:
        return f"{b}B"
    elif b < 1048576:
        return f"{b/1024:.1f}KB"
    elif b < 1073741824:
        return f"{b/1048576:.1f}MB"
    else:
        return f"{b/1073741824:.1f}GB"

app.jinja_env.filters["format_bytes"] = format_bytes


# ── Deploy Utilities ─────────────────────────────────────────────


def find_next_available_port(start=5004):
    """Find next available port not in DB and not listening."""
    db = get_db()
    used_ports = {row[0] for row in db.execute("SELECT port FROM apps WHERE port > 0").fetchall()}
    try:
        result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split("\n"):
            match = re.search(r':(\d+)\s', line)
            if match:
                used_ports.add(int(match.group(1)))
    except Exception:
        pass
    port = start
    while port in used_ports:
        port += 1
    return port


def validate_app_name(name):
    """Validate app name for use as directory/service name."""
    slug = slugify(name)
    if not slug or len(slug) < 2:
        return None, "App name must be at least 2 characters."
    if not re.match(r'^[a-z0-9][a-z0-9-]*$', slug):
        return None, "App name must start with a letter/number and contain only lowercase letters, numbers, and hyphens."
    if '..' in slug or '/' in slug:
        return None, "Invalid app name."
    return slug, None


# ══════════════════════════════════════════════════════════════════
#  HUB — Dashboard, App Registry, Log Viewer
# ══════════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    return redirect("/workspaces")

# ── Workspace Routes ─────────────────────────────────────────────

@app.route("/workspaces")
@login_required
def workspace_list():
    user = current_user()
    workspaces = get_user_workspaces(user["id"])
    # Platform admins see all workspaces
    if user["is_admin"]:
        workspaces = get_db().execute("SELECT * FROM workspaces ORDER BY name").fetchall()
    if len(workspaces) == 1:
        return redirect(f"/workspaces/{workspaces[0]['slug']}/dashboard")
    # Get app counts for each workspace
    db = get_db()
    ws_data = []
    for ws in workspaces:
        app_count = db.execute("SELECT COUNT(*) FROM apps WHERE workspace_id = ?", (ws["id"],)).fetchone()[0]
        ws_data.append({"workspace": ws, "app_count": app_count})
    return render_template("workspaces/list.html", workspaces=ws_data)

@app.route("/workspaces/new", methods=["GET", "POST"])
@login_required
def workspace_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Workspace name is required.", "error")
            return redirect("/workspaces/new")
        ws_slug = slugify(name)
        if not ws_slug:
            flash("Invalid workspace name.", "error")
            return redirect("/workspaces/new")
        db = get_db()
        existing = db.execute("SELECT id FROM workspaces WHERE slug = ?", (ws_slug,)).fetchone()
        if existing:
            flash("A workspace with that name already exists.", "error")
            return redirect("/workspaces/new")
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO workspaces (name, slug, owner_id, created, updated) VALUES (?, ?, ?, ?, ?)",
            (name, ws_slug, session["user_id"], now, now)
        )
        ws_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, created) VALUES (?, ?, ?, ?)",
            (ws_id, session["user_id"], "owner", now)
        )
        db.commit()
        flash(f"Workspace '{name}' created.", "success")
        return redirect(f"/workspaces/{ws_slug}/dashboard")
    return render_template("workspaces/new.html")

@app.route("/workspaces/<ws_slug>/dashboard")
@workspace_required('viewer')
def workspace_dashboard(ws_slug):
    db = get_db()
    apps = db.execute("SELECT * FROM apps WHERE workspace_id = ? ORDER BY name ASC", (g.workspace["id"],)).fetchall()

    # Check health for each app
    claude_dirs = get_claude_code_dirs()
    app_data = []
    for a in apps:
        status = check_app_health(a)
        db.execute("UPDATE apps SET status = ?, last_check = ? WHERE id = ?",
                   (status, datetime.now(timezone.utc).isoformat(), a["id"]))
        log_stats = get_log_stats(a["log_path"])
        server_path = a["server_path"] or ""
        app_data.append({
            "app": a,
            "status": status,
            "log_stats": log_stats,
            "claude_active": server_path.rstrip("/") in claude_dirs,
        })
    db.commit()

    server = get_server_stats()

    return render_template("dashboard.html", apps=app_data, server=server)

@app.route("/workspaces/<ws_slug>/settings", methods=["GET", "POST"])
@workspace_required('owner')
def workspace_settings(ws_slug):
    ws = g.workspace
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        server_ip = request.form.get("server_ip", "").strip()
        server_user = request.form.get("server_user", "").strip()
        if not name:
            flash("Workspace name is required.", "error")
            return redirect(f"/workspaces/{ws_slug}/settings")
        db = get_db()
        db.execute(
            "UPDATE workspaces SET name = ?, server_ip = ?, server_user = ?, updated = ? WHERE id = ?",
            (name, server_ip, server_user, datetime.now(timezone.utc).isoformat(), ws["id"])
        )
        db.commit()
        flash("Workspace settings updated.", "success")
        return redirect(f"/workspaces/{ws_slug}/settings")
    return render_template("workspaces/settings.html")


# ── App CRUD ──────────────────────────────────────────────────────

@app.route("/workspaces/<ws_slug>/apps/new", methods=["GET", "POST"])
@workspace_required('member')
def new_app(ws_slug):
    if request.method == "POST":
        mode = request.form.get("mode", "register")

        if mode == "deploy":
            # Create & Deploy mode (admin only)
            user = current_user()
            if not user or not user["is_admin"]:
                flash("Admin access required for deployment.", "error")
                return redirect(f"/workspaces/{ws_slug}/apps/new")
            return _handle_deploy_form(ws_slug)

        # Quick Register mode (existing behavior)
        name = request.form.get("name", "").strip()
        slug = slugify(name) if name else ""
        url = request.form.get("url", "").strip()
        github_url = request.form.get("github_url", "").strip()
        server_path = request.form.get("server_path", "").strip()
        port = request.form.get("port", "0").strip()
        service_name = request.form.get("service_name", "").strip()
        log_path = request.form.get("log_path", "").strip()
        description = request.form.get("description", "").strip()

        if not name:
            flash("App name is required.", "error")
            return redirect(f"/workspaces/{ws_slug}/apps/new")

        db = get_db()
        existing = db.execute("SELECT id FROM apps WHERE slug = ?", (slug,)).fetchone()
        if existing:
            flash("An app with that name already exists.", "error")
            return redirect(f"/workspaces/{ws_slug}/apps/new")

        now = datetime.now(timezone.utc).isoformat()
        try:
            port_int = int(port) if port else 0
        except ValueError:
            port_int = 0

        db.execute("""
            INSERT INTO apps (name, slug, url, github_url, server_path, port, service_name, log_path, description, workspace_id, created, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, slug, url, github_url, server_path, port_int, service_name, log_path, description, g.workspace["id"], now, now))
        db.commit()
        flash(f"App '{name}' registered.", "success")
        return redirect(f"/workspaces/{ws_slug}/dashboard")

    next_port = find_next_available_port()
    return render_template("apps/new.html", next_port=next_port)


def _handle_deploy_form(ws_slug):
    """Validate the Create & Deploy form and redirect to deploy progress page."""
    name = request.form.get("name", "").strip()
    domain = request.form.get("domain", "").strip().lower()
    description = request.form.get("description", "").strip()
    github_repo = request.form.get("github_repo", "").strip()
    ssl = request.form.get("ssl") == "on"

    slug, error = validate_app_name(name)
    if error:
        flash(error, "error")
        return redirect(f"/workspaces/{ws_slug}/apps/new")

    db = get_db()
    if db.execute("SELECT id FROM apps WHERE slug = ?", (slug,)).fetchone():
        flash("An app with that name already exists.", "error")
        return redirect(f"/workspaces/{ws_slug}/apps/new")

    if not domain or not re.match(r'^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(\.[a-z]{2,})+$', domain):
        flash("Invalid domain format.", "error")
        return redirect(f"/workspaces/{ws_slug}/apps/new")

    app_dir = Path(f"/root/{slug}")
    if app_dir.exists():
        flash(f"Directory /root/{slug} already exists.", "error")
        return redirect(f"/workspaces/{ws_slug}/apps/new")

    port = find_next_available_port()

    deploy_config = json.dumps({
        "name": name,
        "slug": slug,
        "domain": domain,
        "description": description,
        "github_repo": github_repo,
        "ssl": ssl,
        "port": port,
        "ws_slug": ws_slug,
        "workspace_id": g.workspace["id"],
    })
    db.execute("DELETE FROM pending_deploys WHERE slug = ?", (slug,))
    db.execute("INSERT INTO pending_deploys (slug, config, created) VALUES (?, ?, ?)",
               (slug, deploy_config, datetime.now(timezone.utc).isoformat()))
    db.commit()

    return redirect(f"/workspaces/{ws_slug}/apps/deploy/{slug}")


@app.route("/workspaces/<ws_slug>/apps/deploy/<slug>")
@workspace_required('member')
def deploy_progress(ws_slug, slug):
    """Render the deploy progress page."""
    db = get_db()
    row = db.execute("SELECT config FROM pending_deploys WHERE slug = ?", (slug,)).fetchone()
    if not row:
        flash("No pending deployment found.", "error")
        return redirect(f"/workspaces/{ws_slug}/apps/new")
    deploy = json.loads(row["config"])
    return render_template("apps/deploy.html", deploy=deploy)


@app.route("/workspaces/<ws_slug>/apps/deploy/<slug>/stream")
@csrf.exempt
def deploy_stream(ws_slug, slug):
    """SSE endpoint that provisions the app and streams progress."""
    # Auth check (can't use decorator — must not redirect for SSE)
    user = current_user()
    if not user or not user["is_admin"]:
        return "Unauthorized", 403

    db = get_db()
    row = db.execute("SELECT config FROM pending_deploys WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return "No pending deployment", 400
    deploy = json.loads(row["config"])
    db.execute("DELETE FROM pending_deploys WHERE slug = ?", (slug,))
    db.commit()

    def generate():
        name = deploy["name"]
        s = deploy["slug"]
        domain = deploy["domain"]
        port = deploy["port"]
        description = deploy["description"]
        github_repo = deploy["github_repo"]
        ssl = deploy["ssl"]
        app_dir = Path(f"/root/{s}")

        def send_event(step, status, message=""):
            data = json.dumps({"step": step, "status": status, "message": message})
            return f"data: {data}\n\n"

        def run_cmd(cmd, cwd=None, timeout=120):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
                output = (result.stdout + result.stderr).strip()
                output = re.sub(r'\x1b\[[0-9;]*m', '', output)[:2000]
                return result.returncode == 0, output
            except subprocess.TimeoutExpired:
                return False, "Command timed out"
            except Exception as e:
                return False, str(e)

        try:
            # Step 1: Clone baseline
            yield send_event("clone", "running")
            ok, out = run_cmd(["cp", "-r", "/root/baseline", str(app_dir)])
            if not ok:
                yield send_event("clone", "failed", out)
                return
            yield send_event("clone", "done")

            # Step 2: Fresh git repo
            yield send_event("git", "running")
            git_dir = app_dir / ".git"
            if git_dir.exists():
                shutil.rmtree(git_dir)
            pycache = app_dir / "__pycache__"
            if pycache.exists():
                shutil.rmtree(pycache)
            # Remove baseline's venv — we'll create a fresh one
            old_venv = app_dir / "venv"
            if old_venv.exists():
                shutil.rmtree(old_venv)
            run_cmd(["git", "init"], cwd=str(app_dir))
            run_cmd(["git", "add", "."], cwd=str(app_dir))
            run_cmd(["git", "commit", "-m", "Initial commit from baseline"], cwd=str(app_dir))
            yield send_event("git", "done")

            # Step 3: Python venv + deps
            yield send_event("venv", "running")
            ok, out = run_cmd(["python3", "-m", "venv", "venv"], cwd=str(app_dir), timeout=60)
            if not ok:
                yield send_event("venv", "failed", out)
                return
            pip = str(app_dir / "venv" / "bin" / "pip")
            ok, out = run_cmd([pip, "install", "-r", "requirements.txt", "-q"], cwd=str(app_dir), timeout=180)
            if not ok:
                yield send_event("venv", "failed", out)
                return
            yield send_event("venv", "done")

            # Step 4: Generate .env
            yield send_event("env", "running")
            env_content = (
                f"SECRET_KEY={secrets_mod.token_hex(32)}\n"
                f"APP_NAME={name}\n"
                f"SUPPORT_EMAIL=support@revolv.uk\n"
                f"FLASK_ENV=production\n"
                f"FLASK_DEBUG=0\n"
                f"PORT={port}\n"
                f"RESEND_API_KEY=\n"
                f'EMAIL_FROM={name} <noreply@revolv.uk>\n'
            )
            (app_dir / ".env").write_text(env_content)
            yield send_event("env", "done")

            # Step 5: Systemd service
            yield send_event("systemd", "running")
            service_content = f"""[Unit]
Description={name} Flask Application
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={app_dir}
Environment=FLASK_ENV=production
ExecStart={app_dir}/venv/bin/gunicorn -w 4 -b 127.0.0.1:{port} --timeout 30 --keep-alive 5 --access-logfile /var/log/{s}.log app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
            Path(f"/etc/systemd/system/{s}.service").write_text(service_content)
            run_cmd(["systemctl", "daemon-reload"])
            run_cmd(["systemctl", "enable", s])
            ok, out = run_cmd(["systemctl", "start", s])
            if not ok:
                yield send_event("systemd", "failed", out)
                return
            yield send_event("systemd", "done")

            # Step 6: Nginx
            yield send_event("nginx", "running")
            nginx_conf = (
                "server {\n"
                "    listen 80;\n"
                f"    server_name {domain};\n"
                "\n"
                "    location / {\n"
                f"        proxy_pass http://127.0.0.1:{port};\n"
                "        proxy_set_header Host $host;\n"
                "        proxy_set_header X-Real-IP $remote_addr;\n"
                "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                "        proxy_set_header X-Forwarded-Proto $scheme;\n"
                "    }\n"
                "\n"
                "    location /uploads/ {\n"
                f"        alias {app_dir}/uploads/;\n"
                "        expires 7d;\n"
                '        add_header Cache-Control "public, immutable";\n'
                "    }\n"
                "\n"
                "    location /static/ {\n"
                f"        alias {app_dir}/static/;\n"
                "        expires 7d;\n"
                '        add_header Cache-Control "public, immutable";\n'
                "        gzip_static on;\n"
                "    }\n"
                "\n"
                "    client_max_body_size 50M;\n"
                "}\n"
            )
            Path(f"/etc/nginx/sites-available/{s}").write_text(nginx_conf)
            sites_enabled = Path(f"/etc/nginx/sites-enabled/{s}")
            if not sites_enabled.exists():
                sites_enabled.symlink_to(f"/etc/nginx/sites-available/{s}")
            ok, out = run_cmd(["nginx", "-t"])
            if not ok:
                yield send_event("nginx", "failed", f"Nginx config test failed: {out}")
                return
            run_cmd(["systemctl", "reload", "nginx"])
            yield send_event("nginx", "done")

            # Step 7: SSL (optional, non-fatal)
            if ssl:
                yield send_event("ssl", "running")
                ok, out = run_cmd(
                    ["certbot", "--nginx", "-d", domain, "--non-interactive",
                     "--agree-tos", "--redirect", "-m", "admin@revolv.uk"],
                    timeout=120
                )
                if ok:
                    yield send_event("ssl", "done")
                else:
                    yield send_event("ssl", "warning", f"Certbot failed (non-fatal): {out[:500]}")

            # Step 8: GitHub repo (optional, non-fatal)
            if github_repo:
                yield send_event("github", "running")
                run_cmd(["git", "remote", "add", "origin",
                         f"https://github.com/revolv-build/{github_repo}.git"],
                        cwd=str(app_dir))
                ok, out = run_cmd(
                    ["gh", "repo", "create", f"revolv-build/{github_repo}",
                     "--private", "--source", str(app_dir), "--push"],
                    cwd=str(app_dir), timeout=60
                )
                if ok:
                    yield send_event("github", "done")
                else:
                    yield send_event("github", "warning", f"GitHub failed (non-fatal): {out[:500]}")

            # Step 9: Register in DB
            yield send_event("register", "running")
            now = datetime.now(timezone.utc).isoformat()
            github_url = f"https://github.com/revolv-build/{github_repo}" if github_repo else ""
            app_url = f"https://{domain}" if ssl else f"http://{domain}"
            ws_id = deploy.get("workspace_id")
            reg_db = sqlite3.connect(str(DB_PATH))
            reg_db.execute("""
                INSERT INTO apps (name, slug, url, github_url, server_path, port, service_name, log_path, description, status, workspace_id, created, updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, s, app_url, github_url, str(app_dir), port,
                  f"{s}.service", f"/var/log/{s}.log", description,
                  "online", ws_id, now, now))
            reg_db.commit()
            reg_db.close()
            yield send_event("register", "done")

            # Done — pass ws_slug/app_slug for client redirect
            deploy_ws_slug = deploy.get("ws_slug", "")
            yield send_event("complete", "done", json.dumps({"ws_slug": deploy_ws_slug, "slug": s}))

        except Exception as e:
            yield send_event("error", "failed", f"Unexpected error: {str(e)}")

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/workspaces/<ws_slug>/apps/<slug>")
@workspace_required('viewer')
def view_app(ws_slug, slug):
    db = get_db()
    a = db.execute("SELECT * FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"])).fetchone()
    if not a:
        abort(404)

    status = check_app_health(a)
    service_info = get_service_status(a["service_name"])
    log_stats = get_log_stats(a["log_path"])
    recent_logs = read_log_lines(a["log_path"], 50)
    claude_dirs = get_claude_code_dirs()
    server_path = a["server_path"] or ""
    claude_active = server_path.rstrip("/") in claude_dirs

    return render_template("apps/view.html",
                           app=a, status=status, service_info=service_info,
                           log_stats=log_stats, recent_logs=recent_logs,
                           claude_active=claude_active)


@app.route("/workspaces/<ws_slug>/apps/<slug>/edit", methods=["GET", "POST"])
@workspace_required('owner')
def edit_app(ws_slug, slug):
    db = get_db()
    a = db.execute("SELECT * FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"])).fetchone()
    if not a:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        url = request.form.get("url", "").strip()
        github_url = request.form.get("github_url", "").strip()
        server_path = request.form.get("server_path", "").strip()
        port = request.form.get("port", "0").strip()
        service_name = request.form.get("service_name", "").strip()
        log_path = request.form.get("log_path", "").strip()
        description = request.form.get("description", "").strip()

        try:
            port_int = int(port) if port else 0
        except ValueError:
            port_int = 0

        db.execute("""
            UPDATE apps SET name=?, url=?, github_url=?, server_path=?, port=?, service_name=?, log_path=?, description=?, updated=?
            WHERE id=? AND workspace_id=?
        """, (name, url, github_url, server_path, port_int, service_name, log_path, description,
              datetime.now(timezone.utc).isoformat(), a["id"], g.workspace["id"]))
        db.commit()
        flash(f"App '{name}' updated.", "success")
        return redirect(f"/workspaces/{ws_slug}/apps/{slug}")

    return render_template("apps/edit.html", app=a)


@app.route("/workspaces/<ws_slug>/apps/<slug>/delete", methods=["POST"])
@workspace_required('owner')
def delete_app(ws_slug, slug):
    db = get_db()
    db.execute("DELETE FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"]))
    db.commit()
    flash("App removed.", "success")
    return redirect(f"/workspaces/{ws_slug}/dashboard")


@app.route("/workspaces/<ws_slug>/apps/<slug>/logs")
@workspace_required('viewer')
def app_logs(ws_slug, slug):
    db = get_db()
    a = db.execute("SELECT * FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"])).fetchone()
    if not a:
        abort(404)

    num_lines = request.args.get("lines", "200", type=str)
    try:
        num_lines = min(int(num_lines), 1000)
    except ValueError:
        num_lines = 200

    filter_text = request.args.get("filter", "").strip()
    logs = read_log_lines(a["log_path"], num_lines)

    if filter_text:
        logs = [line for line in logs if filter_text.lower() in line.lower()]

    log_stats = get_log_stats(a["log_path"])

    return render_template("apps/logs.html", app=a, logs=logs, log_stats=log_stats,
                           num_lines=num_lines, filter_text=filter_text)


@app.route("/workspaces/<ws_slug>/apps/<slug>/restart", methods=["POST"])
@workspace_required('owner')
def restart_app(ws_slug, slug):
    db = get_db()
    a = db.execute("SELECT * FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"])).fetchone()
    if not a or not a["service_name"]:
        flash("No service configured for this app.", "error")
        return redirect(f"/workspaces/{ws_slug}/apps/{slug}")

    try:
        result = subprocess.run(
            ["systemctl", "restart", a["service_name"]],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            flash(f"Service '{a['service_name']}' restarted.", "success")
        else:
            flash(f"Restart failed: {result.stderr.strip()}", "error")
    except Exception as e:
        flash(f"Restart failed: {e}", "error")

    return redirect(f"/workspaces/{ws_slug}/apps/{slug}")


# ── Terminal Sessions ─────────────────────────────────────────────


@app.route("/workspaces/<ws_slug>/apps/<slug>/terminal")
@workspace_required('member')
def terminal_session(ws_slug, slug):
    _cleanup_stale_terminals()

    db = get_db()
    a = db.execute("SELECT * FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"])).fetchone()
    if not a or not a["server_path"]:
        flash("App has no server path configured.", "error")
        return redirect(f"/workspaces/{ws_slug}/apps/{slug}")

    # Validate server_path is a real directory (prevents path traversal)
    server_path = Path(a["server_path"]).resolve()
    if not server_path.is_dir() or not str(server_path).startswith("/root/"):
        flash("Invalid server path.", "error")
        return redirect(f"/workspaces/{ws_slug}/apps/{slug}")

    # If session already running for this slug, reuse it
    info = _get_terminal_session(slug)
    if info:
        try:
            os.kill(info["pid"], 0)
            return render_template("apps/terminal.html", app=a, token=info["token"])
        except OSError:
            _remove_terminal_session(slug)

    # Enforce concurrent session limit
    if _active_terminal_count() >= TERMINAL_MAX_CONCURRENT:
        flash(f"Maximum {TERMINAL_MAX_CONCURRENT} concurrent terminal sessions. Close one first.", "error")
        return redirect(f"/workspaces/{ws_slug}/apps/{slug}")

    # Find available port in 7000+ range
    port = find_next_available_port(start=7000)

    # 32-byte token (256 bits of entropy)
    token = secrets_mod.token_urlsafe(32)

    # Spawn ttyd — use cwd instead of shell string to prevent command injection
    proc = subprocess.Popen(
        [
            "ttyd", "--writable",
            "--base-path", f"/terminal/{token}",
            "-t", "fontSize=13",
            "-i", "127.0.0.1",
            "-p", str(port),
            "claude"
        ],
        cwd=str(server_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Write nginx location conf
    TERMINAL_CONF_DIR.mkdir(parents=True, exist_ok=True)
    conf_path = TERMINAL_CONF_DIR / f"{token}.conf"
    conf_path.write_text(
        f"location /terminal/{token}/ {{\n"
        f"    proxy_pass http://127.0.0.1:{port}/terminal/{token}/;\n"
        f"    proxy_http_version 1.1;\n"
        f"    proxy_set_header Upgrade $http_upgrade;\n"
        f"    proxy_set_header Connection \"upgrade\";\n"
        f"    proxy_set_header Host $host;\n"
        f"    proxy_set_header X-Real-IP $remote_addr;\n"
        f"    proxy_read_timeout 3600s;\n"
        f"    proxy_send_timeout 3600s;\n"
        f"}}\n"
    )

    # Reload nginx
    subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, timeout=10)

    # Track session (file-based for multi-worker safety)
    _save_terminal_session(slug, {"pid": proc.pid, "port": port, "token": token})

    # Small delay for ttyd to start
    time.sleep(0.5)

    return render_template("apps/terminal.html", app=a, token=token)


@app.route("/workspaces/<ws_slug>/apps/<slug>/terminal/stop", methods=["POST"])
@workspace_required('member')
def terminal_stop(ws_slug, slug):
    info = _get_terminal_session(slug)
    if info:
        try:
            os.kill(info["pid"], signal.SIGTERM)
        except OSError:
            pass
        conf = TERMINAL_CONF_DIR / f"{info['token']}.conf"
        if conf.exists():
            conf.unlink()
        subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, timeout=10)
        _remove_terminal_session(slug)
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": True}
    flash("Terminal session closed.", "success")
    return redirect(f"/workspaces/{ws_slug}/apps/{slug}")


ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB


@app.route("/workspaces/<ws_slug>/apps/<slug>/upload", methods=["POST"])
@workspace_required('member')
def upload_reference(ws_slug, slug):
    """Upload a temp image for use in Claude Code prompts."""
    db = get_db()
    a = db.execute("SELECT * FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"])).fetchone()
    if not a or not a["server_path"]:
        return {"error": "App not found"}, 404

    server_path = Path(a["server_path"]).resolve()
    if not server_path.is_dir() or not str(server_path).startswith("/root/"):
        return {"error": "Invalid server path"}, 400

    f = request.files.get("file")
    if not f or not f.filename:
        return {"error": "No file provided"}, 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        return {"error": f"Only images allowed: {', '.join(ALLOWED_IMAGE_EXT)}"}, 400

    # Read and check size
    data = f.read()
    if len(data) > MAX_IMAGE_SIZE:
        return {"error": "File too large (max 10MB)"}, 400

    # Save to app's tmp/ directory
    tmp_dir = server_path / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    # Unique filename
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', f.filename)
    timestamp = int(time.time())
    filename = f"{timestamp}_{safe_name}"
    filepath = tmp_dir / filename
    filepath.write_bytes(data)

    return {"ok": True, "path": str(filepath), "filename": filename}


@app.route("/workspaces/<ws_slug>/apps/<slug>/uploads", methods=["GET"])
@workspace_required('member')
def list_uploads(ws_slug, slug):
    """List temp uploads for an app."""
    db = get_db()
    a = db.execute("SELECT * FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"])).fetchone()
    if not a or not a["server_path"]:
        return {"files": []}

    tmp_dir = Path(a["server_path"]).resolve() / "tmp"
    if not tmp_dir.exists():
        return {"files": []}

    files = []
    for f in sorted(tmp_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix.lower() in ALLOWED_IMAGE_EXT:
            files.append({"name": f.name, "path": str(f), "size": f.stat().st_size})
    return {"files": files[:20]}


@app.route("/workspaces/<ws_slug>/apps/<slug>/upload/<filename>", methods=["DELETE"])
@workspace_required('member')
def delete_upload(ws_slug, slug, filename):
    """Delete a temp upload."""
    db = get_db()
    a = db.execute("SELECT * FROM apps WHERE slug = ? AND workspace_id = ?", (slug, g.workspace["id"])).fetchone()
    if not a or not a["server_path"]:
        return {"error": "Not found"}, 404

    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    filepath = Path(a["server_path"]).resolve() / "tmp" / safe_name
    if filepath.exists() and str(filepath).startswith("/root/"):
        filepath.unlink()
    return {"ok": True}


# ── API Endpoints ─────────────────────────────────────────────────

@app.route("/api/health")
@csrf.exempt
def api_health():
    """Health check endpoint for this admin hub."""
    return jsonify({"status": "ok", "app": APP_NAME})


@app.route("/api/apps")
@login_required
def api_apps():
    """JSON list of all registered apps with status."""
    db = get_db()
    apps = db.execute("SELECT * FROM apps ORDER BY name ASC").fetchall()
    result = []
    for a in apps:
        status = check_app_health(a)
        result.append({
            "name": a["name"],
            "slug": a["slug"],
            "url": a["url"],
            "status": status,
            "port": a["port"],
            "service_name": a["service_name"],
        })
    return jsonify(result)


@app.route("/api/server")
@login_required
def api_server():
    """JSON server stats."""
    return jsonify(get_server_stats())


# ══════════════════════════════════════════════════════════════════
#  ACCOUNT
# ══════════════════════════════════════════════════════════════════

@app.route("/account")
@login_required
def account_page():
    user = get_user_by_id(session["user_id"])
    return render_template("account.html", user=user)

@app.route("/account/profile", methods=["POST"])
@login_required
def account_profile():
    db = get_db()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    bio = request.form.get("bio", "").strip()
    location = request.form.get("location", "").strip()
    website = request.form.get("website", "").strip()
    existing = get_user_by_email(email)
    if existing and existing["id"] != session["user_id"]:
        flash("Email already in use.", "error")
        return redirect("/account")
    db.execute(
        "UPDATE users SET name=?, email=?, bio=?, location=?, website=? WHERE id=?",
        (name, email, bio, location, website, session["user_id"])
    )
    db.commit()
    session["user_name"] = name
    flash("Profile updated.", "success")
    return redirect("/account")

@app.route("/account/password", methods=["POST"])
@login_required
def account_password():
    db = get_db()
    user = get_user_by_id(session["user_id"])
    if not check_password_hash(user["password_hash"], request.form.get("current", "")):
        flash("Current password incorrect.", "error")
        return redirect("/account")
    new_pw = request.form.get("new", "")
    if len(new_pw) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect("/account")
    db.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (generate_password_hash(new_pw), session["user_id"])
    )
    db.commit()
    flash("Password updated.", "success")
    return redirect("/account")

@app.route("/account/avatar", methods=["POST"])
@login_required
def account_avatar():
    file = request.files.get("avatar")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect("/account")
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        flash("Please upload a JPG, PNG, or WebP image.", "error")
        return redirect("/account")
    avatar_dir = UPLOAD_DIR / "avatars"
    avatar_dir.mkdir(exist_ok=True)
    avatar_name = f"avatar_{session['user_id']}.{ext}"
    file.save(str(avatar_dir / avatar_name))
    db = get_db()
    db.execute("UPDATE users SET avatar_path = ? WHERE id = ?",
               (f"avatars/{avatar_name}", session["user_id"]))
    db.commit()
    flash("Profile photo updated!", "success")
    return redirect("/account")

@app.route("/account/export")
@login_required
def account_export():
    db = get_db()
    uid = session["user_id"]
    user = get_user_by_id(uid)
    data = {
        "account": {
            "id": user["id"], "name": user["name"], "email": user["email"],
            "bio": user["bio"], "location": user["location"], "website": user["website"],
            "created": user["created"]
        },
        "exported_at": datetime.now(timezone.utc).isoformat()
    }
    return json.dumps(data, indent=2), 200, {
        "Content-Type": "application/json",
        "Content-Disposition": f"attachment; filename=my-data-{uid}.json"
    }

@app.route("/account/delete", methods=["POST"])
@login_required
def account_delete():
    uid = session["user_id"]
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.commit()
    session.clear()
    flash("Your account and all data have been permanently deleted.", "success")
    return redirect("/")

# ── File Uploads ──────────────────────────────────────────────────

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)

# ══════════════════════════════════════════════════════════════════
#  PLATFORM ADMIN — /admin/
# ══════════════════════════════════════════════════════════════════

@app.route("/admin")
@platform_admin_required
def admin_dashboard():
    db = get_db()
    tab = request.args.get("tab", "users")
    stats = {
        "total_users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_apps": db.execute("SELECT COUNT(*) FROM apps").fetchone()[0],
        "total_workspaces": db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0],
        "new_users_7d": db.execute(
            "SELECT COUNT(*) FROM users WHERE created >= datetime('now', '-7 days')"
        ).fetchone()[0],
    }
    users = db.execute("SELECT * FROM users ORDER BY created DESC").fetchall()

    activity = []
    for u in db.execute("SELECT id AS user_id, name AS user_name, created FROM users ORDER BY created DESC LIMIT 30").fetchall():
        activity.append({"type": "signup", "user_name": u["user_name"], "user_id": u["user_id"],
                         "detail": None, "created": u["created"]})
    activity.sort(key=lambda x: x["created"], reverse=True)
    activity = activity[:50]

    # Workspace data for admin
    workspaces = []
    for ws in db.execute("SELECT * FROM workspaces ORDER BY name").fetchall():
        owner = db.execute("SELECT name FROM users WHERE id = ?", (ws["owner_id"],)).fetchone()
        member_count = db.execute("SELECT COUNT(*) FROM workspace_members WHERE workspace_id = ?", (ws["id"],)).fetchone()[0]
        app_count = db.execute("SELECT COUNT(*) FROM apps WHERE workspace_id = ?", (ws["id"],)).fetchone()[0]
        workspaces.append({
            "workspace": ws,
            "owner_name": owner["name"] if owner else "Unknown",
            "member_count": member_count,
            "app_count": app_count,
        })

    return render_template("admin.html", stats=stats, users=users, activity=activity, tab=tab, workspaces=workspaces)

@app.route("/admin/users/<int:uid>/toggle-admin", methods=["POST"])
@platform_admin_required
def admin_toggle_platform_admin(uid):
    if uid == session["user_id"]:
        flash("Cannot change your own admin status.", "error")
        return redirect("/admin?tab=users")
    db = get_db()
    user = get_user_by_id(uid)
    if not user:
        flash("User not found.", "error")
        return redirect("/admin?tab=users")
    db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (0 if user["is_admin"] else 1, uid))
    db.commit()
    flash(f"{'Removed' if user['is_admin'] else 'Granted'} admin for {user['name']}.", "success")
    return redirect("/admin?tab=users")

@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@platform_admin_required
def admin_delete_user(uid):
    if uid == session["user_id"]:
        flash("Cannot delete yourself.", "error")
        return redirect("/admin?tab=users")
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.commit()
    flash("User deleted.", "success")
    return redirect("/admin?tab=users")

@app.route("/admin/users/<int:uid>/impersonate", methods=["POST"])
@platform_admin_required
def admin_impersonate(uid):
    user = get_user_by_id(uid)
    if not user:
        flash("User not found.", "error")
        return redirect("/admin?tab=users")
    session["impersonator_id"] = session["user_id"]
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    flash(f"Now viewing as {user['name']}. Use the banner to switch back.", "success")
    return redirect("/workspaces")

@app.route("/admin/stop-impersonating", methods=["POST"])
@login_required
def admin_stop_impersonating():
    real_id = session.pop("impersonator_id", None)
    if real_id:
        user = get_user_by_id(real_id)
        if user:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            flash("Switched back to your admin account.", "success")
            return redirect("/admin")
    return redirect("/workspaces")

# ── Legal Pages ───────────────────────────────────────────────────

@app.route("/terms")
def terms_page():
    return render_template("legal.html", title="Terms of Service",
                           content="These terms of service govern your use of this platform. By using this platform, you agree to these terms. This is a placeholder — update with your actual terms before launch.")

@app.route("/privacy")
def privacy_page():
    return render_template("legal.html", title="Privacy Policy",
                           content="We collect your name, email, and content you create. We do not sell your data to third parties. Cookies are used for session management only. This is a placeholder — update with your actual privacy policy before launch.")

# ── Error Handlers ────────────────────────────────────────────────

@app.errorhandler(404)
def page_not_found(e):
    return render_template("errors/404.html"), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template("errors/500.html"), 500

@app.errorhandler(429)
def rate_limited(e):
    return render_template("errors/429.html"), 429

# ── Boot ──────────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="0.0.0.0", port=DEFAULT_PORT)
