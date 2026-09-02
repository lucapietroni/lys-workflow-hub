"""Smoke test delle pagine HTML della contabilità gestionale (Fase 1)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes_contabilita import get_contabilita_settings
from tests.conftest import get_csrf, login_as_admin


@pytest.fixture
def client(tmp_path: Path, authenticated_app):
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")
    app.dependency_overrides[get_contabilita_settings] = lambda: settings
    c = TestClient(app, follow_redirects=False)
    login_as_admin(c)
    try:
        yield c, settings
    finally:
        app.dependency_overrides.pop(get_contabilita_settings, None)


def test_home_redirige_ai_movimenti(client):
    c, _ = client
    resp = c.get("/contabilita")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/contabilita/movimenti"


def test_lista_movimenti_vuota(client):
    c, _ = client
    resp = c.get("/contabilita/movimenti")
    assert resp.status_code == 200
    assert "Movimenti" in resp.text
    assert "Nessun movimento" in resp.text


def test_categorie_seed_visibili(client):
    c, _ = client
    resp = c.get("/contabilita/categorie")
    assert resp.status_code == 200
    assert "Ricambi" in resp.text
    assert "Manodopera" in resp.text


def test_crea_movimento_manuale(client):
    c, settings = client
    token = get_csrf(c, "/contabilita/movimenti/nuovo")
    resp = c.post(
        "/contabilita/movimenti/nuovo",
        data={
            "csrf_token": token,
            "data": "2026-05-10",
            "importo": "1234,56",
            "tipo": "uscita",
            "categoria_id": "",
            "pratica_id": "766",
            "descrizione": "Stipendio maggio",
            "importo_iva": "",
            "stato": "confermato",
        },
    )
    assert resp.status_code == 303
    repo = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    movimenti = repo.list()
    assert len(movimenti) == 1
    assert movimenti[0].importo == 1234.56
    assert movimenti[0].pratica_id == 766
    assert movimenti[0].descrizione == "Stipendio maggio"


def test_crea_movimento_dati_invalidi_rimostra_form(client):
    c, settings = client
    token = get_csrf(c, "/contabilita/movimenti/nuovo")
    resp = c.post(
        "/contabilita/movimenti/nuovo",
        data={
            "csrf_token": token,
            "data": "10/05/2026",  # formato sbagliato
            "importo": "100",
            "tipo": "uscita",
            "stato": "confermato",
        },
    )
    assert resp.status_code == 200
    assert "Errore" in resp.text
    assert ContabilitaMovimentoRepository(db_path=settings.app_db_path).list() == []


def test_lista_filtra_per_pratica(client):
    c, settings = client
    repo = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    repo.create(data="2026-01-10", importo="100", tipo="uscita", pratica_id=10)
    repo.create(data="2026-01-11", importo="200", tipo="entrata", pratica_id=20)

    resp = c.get("/contabilita/movimenti?pratica_id=10")
    assert resp.status_code == 200
    assert "€ 100.00" in resp.text
    assert "€ 200.00" not in resp.text


def test_crea_categoria(client):
    c, settings = client
    from lys_workflow_hub.core.contabilita_categoria_repository import (
        ContabilitaCategoriaRepository,
    )

    token = get_csrf(c, "/contabilita/categorie")
    resp = c.post(
        "/contabilita/categorie/nuova",
        data={"csrf_token": token, "nome": "Leasing furgone", "tipo": "costo"},
    )
    assert resp.status_code == 303
    nomi = {x.nome for x in ContabilitaCategoriaRepository(db_path=settings.app_db_path).list_all()}
    assert "Leasing furgone" in nomi


def test_route_riservata_ad_admin(tmp_path: Path, authenticated_app):
    """Senza login → redirect a /login (AuthMiddleware)."""
    c = TestClient(app, follow_redirects=False)
    resp = c.get("/contabilita/movimenti")
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]
