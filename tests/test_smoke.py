"""Smoke test minimo: il pacchetto si importa e l'app FastAPI risponde su /health."""
from __future__ import annotations

from fastapi.testclient import TestClient

from lys_workflow_hub import __version__
from lys_workflow_hub.main import app
from tests.conftest import login_as_admin


def test_version_exposed() -> None:
    assert isinstance(__version__, str) and __version__


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_root_returns_html(authenticated_app) -> None:
    client = TestClient(app)
    login_as_admin(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "LYS Workflow Hub" in response.text
