"""Test del portale utenti esterni (v3.0 fase 3, /portale)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.wincar_repository import (
    Cliente,
    CompagniaCliente,
    Controparte,
    Pratica,
    Sinistro,
    Veicolo,
)
from lys_workflow_hub.config import Settings
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes_portale import (
    get_assegnazioni_repo,
    get_portale_settings,
    get_wincar_repo,
)
from tests.conftest import login_as, login_as_admin


def _sample_pratica(numero: int) -> Pratica:
    return Pratica(
        numero=numero,
        data_creazione=datetime(2026, 5, 1, 0, 0),
        cliente=Cliente("ROSSI MARIO", None, None, None, None, None, None, None, None, None),
        veicolo=Veicolo("AB123CD", "FIAT", "Punto", None),
        sinistro=Sinistro(None, None, None, None, None, None, None),
        controparte=Controparte(None, None, None, None, None, None, None, None),
        assicurazione_cliente=CompagniaCliente(None, None, None, None, None, None, None),
    )


@pytest.fixture
def portale_client(tmp_path: Path, authenticated_app):
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=tmp_path / "assegnazioni.db")
    wincar_repo = MagicMock()
    wincar_repo.get_pratica.side_effect = lambda n: _sample_pratica(n) if n == 766 else None
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")

    app.dependency_overrides[get_assegnazioni_repo] = lambda: assegnazioni_repo
    app.dependency_overrides[get_wincar_repo] = lambda: wincar_repo
    app.dependency_overrides[get_portale_settings] = lambda: settings
    try:
        yield assegnazioni_repo
    finally:
        app.dependency_overrides.pop(get_assegnazioni_repo, None)
        app.dependency_overrides.pop(get_wincar_repo, None)
        app.dependency_overrides.pop(get_portale_settings, None)


def test_portale_vuoto_senza_assegnazioni(authenticated_app, portale_client) -> None:
    client = TestClient(app)
    login_as_admin(client)  # admin non ha pratiche assegnate: lista vuota, non errore
    resp = client.get("/portale")
    assert resp.status_code == 200
    assert "Nessuna pratica" in resp.text


def test_portale_mostra_solo_pratiche_assegnate(authenticated_app, portale_client) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    portale_client.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app)
    login_as(client, "agenzia@esempio.it", "password1234")
    resp = client.get("/portale")
    assert resp.status_code == 200
    assert "766" in resp.text
    assert "ROSSI MARIO" in resp.text


def test_portale_non_mostra_pratiche_di_altri_utenti(authenticated_app, portale_client) -> None:
    esterno_a = authenticated_app.create(
        email="a@esempio.it", password="password1234", ruolo="esterno"
    )
    esterno_b = authenticated_app.create(
        email="b@esempio.it", password="password1234", ruolo="esterno"
    )
    portale_client.assegna(766, esterno_a.id, assegnato_da=1)

    client = TestClient(app)
    login_as(client, "b@esempio.it", "password1234")
    resp = client.get("/portale")
    assert resp.status_code == 200
    assert "766" not in resp.text
