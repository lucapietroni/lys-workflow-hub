"""Test del widget "Collaboratori esterni" su /pratiche/{numero} (v3.0 fase 3)."""
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


def _sample_pratica(numero: int = 766) -> Pratica:
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
def client_pratica(tmp_path: Path, authenticated_app):
    repo = MagicMock()
    repo.get_pratica.return_value = _sample_pratica()
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")

    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_app_settings] = lambda: settings
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_repository, None)
        app.dependency_overrides.pop(get_app_settings, None)


def test_pratica_detail_mostra_form_assegnazione(client_pratica) -> None:
    resp = client_pratica.get("/pratiche/766")
    assert resp.status_code == 200
    assert "Collaboratori esterni" in resp.text
    assert "Nessun utente esterno disponibile" in resp.text


def test_assegna_e_rimuovi_collaboratore(client_pratica, authenticated_app) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )

    token = get_csrf(client_pratica, "/pratiche/766")
    resp = client_pratica.post(
        "/pratiche/766/assegna", data={"utente_id": esterno.id, "csrf_token": token}
    )
    assert resp.status_code == 303

    resp = client_pratica.get("/pratiche/766")
    assert "Agenzia" in resp.text
    assert "agenzia@esempio.it" in resp.text

    resp = client_pratica.post(
        f"/pratiche/766/assegna/{esterno.id}/rimuovi", data={"csrf_token": token}
    )
    assert resp.status_code == 303

    resp = client_pratica.get("/pratiche/766")
    assert "Nessun collaboratore assegnato" in resp.text
