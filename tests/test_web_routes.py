"""Test delle pagine HTML (smoke) usando un repository mockato."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.core.wincar_repository import (
    Cliente,
    CompagniaCliente,
    Controparte,
    Pratica,
    PraticaSummary,
    Sinistro,
    Veicolo,
)
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes import get_repository
from tests.conftest import login_as_admin


def _sample_summary() -> PraticaSummary:
    return PraticaSummary(
        numero=766,
        cliente_nominativo="rossi mario",
        targa="AB123CD",
        marca="FIAT",
        modello="Punto",
        data_sinistro=date(2026, 5, 8),
        codice_fiscale="RSSMRA80A01H501U",
    )


def _sample_pratica() -> Pratica:
    return Pratica(
        numero=766,
        data_creazione=datetime(2026, 5, 10, 14, 30),
        cliente=Cliente(
            nominativo="rossi mario",
            codice_fiscale="RSSMRA80A01H501U",
            partita_iva=None,
            via="Via Roma 12",
            citta="Roma",
            cap="00100",
            provincia="RM",
            telefono="0612345678",
            cellulare="3331234567",
            email="mario.rossi@example.com",
        ),
        veicolo=Veicolo(targa="AB123CD", marca="FIAT", modello="Punto", telaio="ZFA"),
        sinistro=Sinistro(
            data=date(2026, 5, 8),
            ora="10:15",
            comune="Roma",
            via="Via Nazionale",
            dinamica="Tamponamento posteriore al semaforo.",
            numero="2026-AB-001",
            tipo="C",
        ),
        controparte=Controparte(
            proprietario="BIANCHI LUCA",
            conducente="BIANCHI LUCA",
            veicolo_descrizione="BMW Serie 1",
            targa="XY987ZW",
            indirizzo=None,
            citta=None,
            compagnia="Generali Italia SpA",
            numero_polizza="POL-99887766",
        ),
        assicurazione_cliente=CompagniaCliente(
            nome="Allianz",
            indirizzo=None,
            citta=None,
            cap=None,
            provincia=None,
            numero_polizza="POL-11223344",
            agenzia=None,
        ),
    )


@pytest.fixture
def client_with_mock_repo(authenticated_app):
    repo = MagicMock()

    def _override():
        return repo

    app.dependency_overrides[get_repository] = _override
    try:
        client = TestClient(app)
        login_as_admin(client)
        yield client, repo
    finally:
        app.dependency_overrides.pop(get_repository, None)


def test_home_no_query_mostra_ultime_pratiche(client_with_mock_repo):
    client, repo = client_with_mock_repo
    repo.search_pratiche.return_value = [_sample_summary()]
    response = client.get("/")
    assert response.status_code == 200
    assert "Gestione pratiche sinistri" in response.text
    assert "Ultime pratiche" in response.text
    assert "rossi mario" in response.text
    repo.search_pratiche.assert_called_once_with(limit=20)


def test_home_numeric_query_triggers_numero_search(client_with_mock_repo):
    client, repo = client_with_mock_repo
    repo.search_pratiche.return_value = [_sample_summary()]
    response = client.get("/?q=766")
    assert response.status_code == 200
    repo.search_pratiche.assert_called_once_with(numero=766, limit=20)
    assert "rossi mario" in response.text
    assert "AB123CD" in response.text


def test_home_alphabetic_query_triggers_cognome_search(client_with_mock_repo):
    client, repo = client_with_mock_repo
    repo.search_pratiche.return_value = []
    client.get("/?q=rossi")
    repo.search_pratiche.assert_called_once_with(cognome="rossi", limit=20)


def test_home_targa_query_triggers_targa_search(client_with_mock_repo):
    client, repo = client_with_mock_repo
    repo.search_pratiche.return_value = []
    client.get("/?q=AB123CD")
    repo.search_pratiche.assert_called_once_with(targa="AB123CD", limit=20)


def test_pratica_detail_renders_all_sections(client_with_mock_repo):
    client, repo = client_with_mock_repo
    repo.get_pratica.return_value = _sample_pratica()
    response = client.get("/pratiche/766")
    assert response.status_code == 200
    assert "N. 766" in response.text
    for section in ("Cliente", "Veicolo", "Sinistro", "Controparte", "Assicurazione cliente"):
        assert section in response.text
    assert "RSSMRA80A01H501U" in response.text
    assert "Tamponamento posteriore al semaforo." in response.text
    assert "Allianz" in response.text


def test_pratica_detail_404_when_not_found(client_with_mock_repo):
    client, repo = client_with_mock_repo
    repo.get_pratica.return_value = None
    response = client.get("/pratiche/99999")
    assert response.status_code == 404
    assert "Pratica non trovata" in response.text
    assert "99999" in response.text
