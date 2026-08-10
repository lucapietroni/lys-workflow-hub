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
from lys_workflow_hub.core.pratica_stato_repository import PraticaStatoRepository
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
from tests.conftest import get_csrf, login_as, login_as_admin


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


def test_portale_mostra_stato_aperta_di_default(
    tmp_path: Path, authenticated_app, portale_client
) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    portale_client.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app)
    login_as(client, "agenzia@esempio.it", "password1234")
    resp = client.get("/portale")
    assert resp.status_code == 200
    assert "Aperta" in resp.text
    assert "row-chiusa" not in resp.text


def test_portale_evidenzia_pratica_chiusa(
    tmp_path: Path, authenticated_app, portale_client
) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    portale_client.assegna(766, esterno.id, assegnato_da=1)
    stato_repo = PraticaStatoRepository(db_path=tmp_path / "app.db")
    stato_repo.set_stato(766, "chiusa", changed_by="Admin Test")

    client = TestClient(app)
    login_as(client, "agenzia@esempio.it", "password1234")
    resp = client.get("/portale")
    assert resp.status_code == 200
    assert "Chiusa" in resp.text
    assert "row-chiusa" in resp.text


def test_portale_lista_ha_filtro_ricerca_e_stato(authenticated_app, portale_client) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    portale_client.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app)
    login_as(client, "agenzia@esempio.it", "password1234")
    resp = client.get("/portale")
    assert resp.status_code == 200
    assert 'id="portale-filtro-testo"' in resp.text
    assert 'id="portale-filtro-stato"' in resp.text
    assert 'data-numero="766"' in resp.text
    assert 'data-cliente="rossi mario"' in resp.text
    assert 'data-targa="ab123cd"' in resp.text
    assert 'data-stato="aperta"' in resp.text
    # tutte le voci di stato disponibili (usate come opzioni del filtro)
    assert "Periziata" in resp.text
    assert "In trattativa" in resp.text


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


# --------------------------------------------------------------------------- #
#  Export CSV pratiche (portale esterno)
# --------------------------------------------------------------------------- #


def _sample_pratica_900() -> Pratica:
    return Pratica(
        numero=900,
        data_creazione=datetime(2026, 6, 1, 0, 0),
        cliente=Cliente("VERDI LUIGI", None, None, None, None, None, None, None, None, None),
        veicolo=Veicolo("XY987ZW", "BMW", "Serie 1", None),
        sinistro=Sinistro(None, None, None, None, None, None, None),
        controparte=Controparte(None, None, None, None, None, None, None, None),
        assicurazione_cliente=CompagniaCliente(None, None, None, None, None, None, None),
    )


def _autorizza_esterno_su_766_e_900(authenticated_app, portale_client):
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    portale_client.assegna(766, esterno.id, assegnato_da=1)
    portale_client.assegna(900, esterno.id, assegnato_da=1)
    wincar_repo = app.dependency_overrides[get_wincar_repo]()
    wincar_repo.get_pratica.side_effect = (
        lambda n: _sample_pratica(n) if n == 766 else (_sample_pratica_900() if n == 900 else None)
    )


def test_portale_esporta_pagina_mostra_checkbox_e_filtro_stato(
    authenticated_app, portale_client
) -> None:
    _autorizza_esterno_su_766_e_900(authenticated_app, portale_client)
    client = TestClient(app)
    login_as(client, "agenzia@esempio.it", "password1234")
    resp = client.get("/portale/esporta")
    assert resp.status_code == 200
    assert 'id="esporta-seleziona-tutto"' in resp.text
    assert 'class="esporta-filtro-stato"' in resp.text
    assert 'name="numero" value="766"' in resp.text
    assert 'name="numero" value="900"' in resp.text
    # Filtro collaboratore: solo admin, non nel portale esterno.
    assert "esporta-filtro-collaboratore" not in resp.text


def test_portale_esporta_csv_tutte_senza_selezione(authenticated_app, portale_client) -> None:
    _autorizza_esterno_su_766_e_900(authenticated_app, portale_client)
    client = TestClient(app)
    login_as(client, "agenzia@esempio.it", "password1234")

    token = get_csrf(client, "/portale/esporta")
    resp = client.post("/portale/esporta.csv", data={"csrf_token": token})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.content.decode("utf-8-sig")
    assert "Numero;Cliente;Targa;Veicolo;Data sinistro;Stato" in body
    assert "766;ROSSI MARIO;AB123CD;FIAT Punto;;Aperta" in body
    assert "900;VERDI LUIGI;XY987ZW;BMW Serie 1;;Aperta" in body


def test_portale_esporta_csv_selezione_filtra_le_righe(
    authenticated_app, portale_client
) -> None:
    _autorizza_esterno_su_766_e_900(authenticated_app, portale_client)
    client = TestClient(app)
    login_as(client, "agenzia@esempio.it", "password1234")

    token = get_csrf(client, "/portale/esporta")
    resp = client.post(
        "/portale/esporta.csv", data={"csrf_token": token, "numero": ["766"]}
    )

    body = resp.content.decode("utf-8-sig")
    assert "766;ROSSI MARIO" in body
    assert "900;VERDI LUIGI" not in body


def test_portale_esporta_csv_non_include_pratiche_non_assegnate(
    tmp_path: Path, authenticated_app, portale_client
) -> None:
    """L'export deve rispettare lo stesso scoping di /portale — un utente
    non deve poter esportare una pratica non sua passando il suo numero
    a mano nella selezione."""
    esterno_a = authenticated_app.create(
        email="a@esempio.it", password="password1234", ruolo="esterno"
    )
    esterno_b = authenticated_app.create(
        email="b@esempio.it", password="password1234", ruolo="esterno"
    )
    portale_client.assegna(766, esterno_a.id, assegnato_da=1)
    wincar_repo = app.dependency_overrides[get_wincar_repo]()
    wincar_repo.get_pratica.side_effect = lambda n: _sample_pratica(n) if n == 766 else None

    client = TestClient(app)
    login_as(client, "b@esempio.it", "password1234")
    token = get_csrf(client, "/portale/esporta")
    resp = client.post(
        "/portale/esporta.csv", data={"csrf_token": token, "numero": ["766"]}
    )

    body = resp.content.decode("utf-8-sig")
    assert "766" not in body
    assert "Numero;Cliente;Targa;Veicolo;Data sinistro;Stato" in body


def test_portale_esporta_csv_filtro_multi_stato(
    tmp_path: Path, authenticated_app, portale_client
) -> None:
    _autorizza_esterno_su_766_e_900(authenticated_app, portale_client)
    stato_repo = PraticaStatoRepository(db_path=tmp_path / "app.db")
    stato_repo.set_stato(766, "in_gestione", changed_by="Admin")
    stato_repo.set_stato(900, "chiusa", changed_by="Admin")

    client = TestClient(app)
    login_as(client, "agenzia@esempio.it", "password1234")
    token = get_csrf(client, "/portale/esporta")
    resp = client.post(
        "/portale/esporta.csv",
        data={"csrf_token": token, "stato": ["in_gestione", "chiusa"]},
    )

    body = resp.content.decode("utf-8-sig")
    assert "766;ROSSI MARIO" in body
    assert "900;VERDI LUIGI" in body
