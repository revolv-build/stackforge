"""
Seed script — pre-populates with the server's actual apps.
Run with: make seed  (or: python seed.py)
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).parent / "data" / "app.db"

def seed():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA foreign_keys=ON")
    now = datetime.now(timezone.utc).isoformat()

    # Check if apps already seeded
    count = db.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
    if count > 0:
        print("Apps already registered. Skipping seed.")
        db.close()
        return

    print("Seeding server apps...")

    apps = [
        {
            "name": "Community",
            "slug": "community",
            "url": "https://community.revolv.uk",
            "github_url": "https://github.com/revolv-build/community",
            "server_path": "/root/community",
            "port": 5001,
            "service_name": "community.service",
            "log_path": "/var/log/community.log",
            "description": "Multi-tenant community platform with discussions, events, resources, jobs, and polls.",
        },
        {
            "name": "Brandkit",
            "slug": "brandkit",
            "url": "https://brandkit.revolv.uk",
            "github_url": "https://github.com/revolv-build/brandkit",
            "server_path": "/root/brandkit",
            "port": 5000,
            "service_name": "brandkit.service",
            "log_path": "/var/log/brandkit.log",
            "description": "Brand asset management platform.",
        },
        {
            "name": "Ganttly",
            "slug": "ganttly",
            "url": "",
            "github_url": "https://github.com/revolv-build/ganttly",
            "server_path": "/root/ganttly",
            "port": 0,
            "service_name": "",
            "log_path": "",
            "description": "Project management app (in development).",
        },
        {
            "name": "Baseline Admin",
            "slug": "baselineadmin",
            "url": "https://admin.revolv.uk",
            "github_url": "https://github.com/revolv-build/baselineadmin",
            "server_path": "/root/baselineadmin",
            "port": 5002,
            "service_name": "baselineadmin.service",
            "log_path": "/var/log/baselineadmin.log",
            "description": "Central admin hub — this app. Server monitoring and app management.",
        },
    ]

    for a in apps:
        db.execute("""
            INSERT INTO apps (name, slug, url, github_url, server_path, port, service_name, log_path, description, created, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (a["name"], a["slug"], a["url"], a["github_url"], a["server_path"],
              a["port"], a["service_name"], a["log_path"], a["description"], now, now))
        print(f"  Registered: {a['name']} ({a['slug']})")

    db.commit()
    db.close()
    print(f"\nSeeded {len(apps)} apps.")
    print("\nDefault login: admin@example.com / changeme")

if __name__ == "__main__":
    seed()
