"""
Hub tests — app registry CRUD, dashboard, API.
"""


def test_dashboard_loads(auth_client):
    """Dashboard shows server stats and app grid."""
    resp = auth_client.get("/dashboard")
    assert resp.status_code == 200
    assert b"Server Overview" in resp.data


def test_register_app(auth_client):
    """Can register a new app."""
    resp = auth_client.post("/apps/new", data={
        "name": "Test App",
        "url": "https://test.example.com",
        "github_url": "https://github.com/test/app",
        "server_path": "/root/testapp",
        "port": "5099",
        "service_name": "",
        "log_path": "",
        "description": "A test application.",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Test App" in resp.data


def test_view_app(auth_client):
    """Can view a registered app."""
    auth_client.post("/apps/new", data={
        "name": "View Test",
        "url": "",
        "github_url": "",
        "server_path": "/root/viewtest",
        "port": "0",
        "service_name": "",
        "log_path": "",
        "description": "For viewing.",
    })
    resp = auth_client.get("/apps/view-test")
    assert resp.status_code == 200
    assert b"View Test" in resp.data


def test_edit_app(auth_client):
    """Can edit a registered app."""
    auth_client.post("/apps/new", data={
        "name": "Edit Test",
        "url": "",
        "github_url": "",
        "server_path": "",
        "port": "0",
        "service_name": "",
        "log_path": "",
        "description": "",
    })
    resp = auth_client.post("/apps/edit-test/edit", data={
        "name": "Edit Test Updated",
        "url": "https://updated.example.com",
        "github_url": "",
        "server_path": "",
        "port": "0",
        "service_name": "",
        "log_path": "",
        "description": "Updated description.",
    }, follow_redirects=True)
    assert resp.status_code == 200


def test_delete_app(auth_client):
    """Can delete a registered app."""
    auth_client.post("/apps/new", data={
        "name": "Delete Test",
        "url": "",
        "github_url": "",
        "server_path": "",
        "port": "0",
        "service_name": "",
        "log_path": "",
        "description": "",
    })
    resp = auth_client.post("/apps/delete-test/delete", follow_redirects=True)
    assert resp.status_code == 200


def test_api_health(client):
    """Health endpoint responds without auth."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


def test_api_apps_requires_auth(client):
    """API apps endpoint requires login."""
    resp = client.get("/api/apps")
    assert resp.status_code == 302


def test_api_server_requires_auth(client):
    """API server endpoint requires login."""
    resp = client.get("/api/server")
    assert resp.status_code == 302


def test_app_logs_page(auth_client):
    """Log viewer loads for a registered app."""
    auth_client.post("/apps/new", data={
        "name": "Logviewer Test App",
        "url": "",
        "github_url": "",
        "server_path": "",
        "port": "0",
        "service_name": "",
        "log_path": "",
        "description": "",
    })
    resp = auth_client.get("/apps/logviewer-test-app/logs", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Logs: Logviewer Test App" in resp.data
