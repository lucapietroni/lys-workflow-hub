"""Smoke test minimo: il pacchetto si importa e l'app FastAPI risponde su /health."""
from __future__ import annotations

from fastapi.testclient import TestClient

from lys_workflow_hub import __version__
from lys_workflow_hub.main import app


def test_version_exposed() -> None:
    assert __version__ == "0.1.0"


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_root_returns_html() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "LYS Workflow Hub" in response.text
