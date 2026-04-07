"""
Test configuration — creates a fresh database for each test session.
"""

import os
import tempfile
import pytest

# Set test environment before importing app
os.environ["FLASK_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"

# Use a temp database for tests so we don't pollute the real one
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["TEST_DB_PATH"] = _tmp_db.name

import app as app_module
from pathlib import Path

# Override DB_PATH before init_db runs in the fixture
app_module.DB_PATH = Path(_tmp_db.name)


@pytest.fixture(scope="session")
def app():
    """Create application for testing with a fresh database."""
    app_module.init_db()

    flask_app = app_module.app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    app_module.limiter.enabled = False
    yield flask_app

    # Cleanup temp db
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


@pytest.fixture
def auth_client(client):
    """Create a test client that's logged in as admin."""
    client.post("/login", data={
        "email": "admin@example.com",
        "password": "changeme",
        "_ts": "0",
    })
    return client


@pytest.fixture
def user_client(client):
    """Create a test client logged in as a regular user."""
    client.post("/register", data={
        "name": "Test User",
        "email": "test@example.com",
        "password": "testpassword123",
        "password_confirm": "testpassword123",
        "_ts": "0",
    })
    return client
