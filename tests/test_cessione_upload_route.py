"""Test della route di upload scansione firmata con repository mockato."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.wincar_repository import (
    Cliente,
    CompagniaCliente,
    Controparte,
    Pratica,
    Sinistro,
    Veicolo,
)
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes import get_app_settings, get_repository
from tests.conftest import get_csrf, login_as_admin


MINI_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer\n%%EOF\n"


def _sample_pratica(numero: int = 12) -> Pratica:
    return Pratica(
        numero=numero,
        data_creazione=datetime(2026, 5, 1, 0, 0),
        cliente=Cliente("ROSSI MARIO", None, None, None, None, None, None, None, None, None),
        veicolo=Veicolo("AB123CD", None, None, None),
        sinistro=Sinistro(None, None, None, None, None, None, None),
        controparte=Controparte(None, None, None, None, None, None, None, None),
        assicurazione_cliente=CompagniaCliente(None, None, None, None, None, None, None),
    )


@pytest.fixture
def client_with_mocks(tmp_path: Path, authenticated_app):
    repo = MagicMock()
    repo.get_pratica.return_value = _sample_pratica()
    settings = Settings(
        wincar_archivio=tmp_path,
        app_archivio_cessioni=tmp_path / "centrale",
    )

    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_app_settings] = lambda: settings
    try:
        client = TestClient(app)
        login_as_admin(client)
        yield client, repo, settings, tmp_path
    finally:
        app.dependency_overrides.pop(get_repository, None)
        app.dependency_overrides.pop(get_app_settings, None)


def test_upload_signed_pdf_saves_to_pratica_folder(client_with_mocks):
    client, repo, settings, tmp_path = client_with_mocks
    response = client.post(
        "/pratiche/12/cessione/firmata",
        data={"csrf_token": get_csrf(client, "/pratiche/12")},
        files={"file": ("scan.pdf", MINI_PDF, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303  # redirect dopo POST
    target_dir = tmp_path / "Pratiche" / "12" / "Pubblici" / "Allegati"
    assert target_dir.exists()
    saved = list(target_dir.glob("Cessione_credito_*_firmata.pdf"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == MINI_PDF


def test_upload_signed_pdf_redirects_with_uploaded_param(client_with_mocks):
    client, *_ = client_with_mocks
    response = client.post(
        "/pratiche/12/cessione/firmata",
        data={"csrf_token": get_csrf(client, "/pratiche/12")},
        files={"file": ("scan.pdf", MINI_PDF, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/pratiche/12?uploaded=Cessione_credito_")


def test_upload_rejects_non_pdf_content_type(client_with_mocks):
    client, *_ = client_with_mocks
    response = client.post(
        "/pratiche/12/cessione/firmata",
        data={"csrf_token": get_csrf(client, "/pratiche/12")},
        files={"file": ("scan.jpg", b"\x89PNG", "image/jpeg")},
    )
    assert response.status_code == 400


def test_upload_rejects_invalid_pdf_bytes(client_with_mocks):
    client, *_ = client_with_mocks
    response = client.post(
        "/pratiche/12/cessione/firmata",
        data={"csrf_token": get_csrf(client, "/pratiche/12")},
        files={"file": ("scan.pdf", b"not a real pdf", "application/pdf")},
    )
    assert response.status_code == 400


def test_upload_404_when_pratica_missing(client_with_mocks):
    client, repo, *_ = client_with_mocks
    token = get_csrf(client, "/pratiche/12")
    repo.get_pratica.return_value = None
    response = client.post(
        "/pratiche/9999/cessione/firmata",
        data={"csrf_token": token},
        files={"file": ("scan.pdf", MINI_PDF, "application/pdf")},
    )
    assert response.status_code == 404


def test_upload_senza_csrf_token_rifiutato(client_with_mocks):
    """Le route multipart verificano il CSRF da sole (il middleware le
    esclude, vedi `_is_multipart` in `web/auth.py`) — deve comunque bloccare
    un upload senza token, non limitarsi a inoltrarlo."""
    client, *_ = client_with_mocks
    response = client.post(
        "/pratiche/12/cessione/firmata",
        files={"file": ("scan.pdf", MINI_PDF, "application/pdf")},
    )
    assert response.status_code == 403


def test_upload_con_csrf_token_falso_rifiutato(client_with_mocks):
    client, *_ = client_with_mocks
    response = client.post(
        "/pratiche/12/cessione/firmata",
        data={"csrf_token": "token-falso"},
        files={"file": ("scan.pdf", MINI_PDF, "application/pdf")},
    )
    assert response.status_code == 403
