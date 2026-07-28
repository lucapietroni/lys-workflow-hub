"""Ruolo "supervisore" (v4.11.0): vede TUTTE le pratiche assegnate a
qualunque utente esterno (non solo le proprie), stesso portale
dell'esterno ma in sola lettura — nessuna route di scrittura deve mai
accettare un suo POST, nessun form di scrittura deve comparire nel
dettaglio pratica.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.utenti_repository import UtentiRepository
from lys_workflow_hub.core.wincar_repository import (
    Cliente,
    CompagniaCliente,
    Controparte,
    Pratica,
    Sinistro,
    Veicolo,
)
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes_portale import (
    get_assegnazioni_repo,
    get_portale_settings,
    get_wincar_repo,
)
from tests.conftest import get_csrf, login_as


def _pratica(numero: int) -> Pratica:
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
def supervisore_setup(tmp_path: Path, authenticated_app):
    """Due esterni (A assegnato alla 700, B assegnato alla 701) + un
    supervisore senza assegnazioni proprie."""
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=tmp_path / "app.db")
    wincar_repo = MagicMock()
    wincar_repo.get_pratica.side_effect = lambda n: _pratica(n) if n in (700, 701) else None
    settings = Settings(
        wincar_archivio=tmp_path,
        app_db_path=tmp_path / "app.db",
        ntfy_server="https://ntfy.sh",
        ntfy_topic="topic-segreto",
    )

    esterno_a = authenticated_app.create(
        email="agenzia-a@esempio.it", password="password1234", nome="Agenzia A", ruolo="esterno"
    )
    esterno_b = authenticated_app.create(
        email="agenzia-b@esempio.it", password="password1234", nome="Agenzia B", ruolo="esterno"
    )
    supervisore = authenticated_app.create(
        email="supervisore@esempio.it",
        password="password1234",
        nome="Super Visore",
        ruolo="supervisore",
    )
    assegnazioni_repo.assegna(700, esterno_a.id, assegnato_da=1)
    assegnazioni_repo.assegna(701, esterno_b.id, assegnato_da=1)

    app.dependency_overrides[get_assegnazioni_repo] = lambda: assegnazioni_repo
    app.dependency_overrides[get_wincar_repo] = lambda: wincar_repo
    app.dependency_overrides[get_portale_settings] = lambda: settings
    try:
        yield assegnazioni_repo, settings, supervisore
    finally:
        app.dependency_overrides.pop(get_assegnazioni_repo, None)
        app.dependency_overrides.pop(get_wincar_repo, None)
        app.dependency_overrides.pop(get_portale_settings, None)


# --------------------------------------------------------------------------- #
#  Ruolo — creazione, property, repository
# --------------------------------------------------------------------------- #


def test_crea_utente_ruolo_supervisore(utenti_repo: UtentiRepository) -> None:
    u = utenti_repo.create(
        email="sup@esempio.it", password="password1234", ruolo="supervisore"
    )
    assert u.ruolo == "supervisore"
    assert u.is_supervisore is True
    assert u.is_admin is False


def test_list_pratica_numeri_assegnate_tutte_dedup(tmp_path: Path) -> None:
    repo = PraticaAssegnazioniRepository(db_path=tmp_path / "app.db")
    repo.assegna(100, utente_id=1, assegnato_da=None)
    repo.assegna(200, utente_id=2, assegnato_da=None)
    repo.assegna(100, utente_id=3, assegnato_da=None)  # stessa pratica, altro utente

    numeri = repo.list_pratica_numeri_assegnate()
    assert set(numeri) == {100, 200}
    assert len(numeri) == 2  # niente duplicati nonostante due assegnazioni sulla 100


def test_list_pratica_numeri_assegnate_vuota_senza_assegnazioni(tmp_path: Path) -> None:
    repo = PraticaAssegnazioniRepository(db_path=tmp_path / "app.db")
    assert repo.list_pratica_numeri_assegnate() == []


# --------------------------------------------------------------------------- #
#  /portale — lista e calendario vedono TUTTE le pratiche assegnate
# --------------------------------------------------------------------------- #


def test_portale_list_supervisore_vede_pratiche_di_tutti_gli_esterni(
    supervisore_setup,
) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")

    resp = client.get("/portale")
    assert resp.status_code == 200
    assert "701" in resp.text
    assert "700" in resp.text


def test_portale_list_esterno_vede_solo_le_proprie(supervisore_setup) -> None:
    # Controllo di non-regressione: un esterno normale continua a vedere
    # solo le pratiche assegnate a lui, non quelle dell'altro esterno.
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia-a@esempio.it", "password1234")

    resp = client.get("/portale")
    assert resp.status_code == 200
    assert "700" in resp.text
    assert "701" not in resp.text


# --------------------------------------------------------------------------- #
#  /portale/pratiche/{numero} — accesso in lettura
# --------------------------------------------------------------------------- #


def test_supervisore_vede_dettaglio_pratica_di_un_altro_utente(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")

    resp = client.get("/portale/pratiche/700")
    assert resp.status_code == 200
    assert "ROSSI MARIO" in resp.text


def test_supervisore_404_su_pratica_non_assegnata_a_nessuno(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")

    resp = client.get("/portale/pratiche/999")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
#  Nessun form di scrittura nel dettaglio, per il supervisore
# --------------------------------------------------------------------------- #


def test_supervisore_non_vede_form_di_scrittura(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")

    resp = client.get("/portale/pratiche/700")
    assert resp.status_code == 200
    assert 'action="/portale/pratiche/700/note"' not in resp.text
    assert 'action="/portale/pratiche/700/eventi"' not in resp.text
    assert 'action="/portale/pratiche/700/stato"' not in resp.text
    assert 'action="/portale/pratiche/700/foto"' not in resp.text
    assert 'action="/portale/pratiche/700/documenti"' not in resp.text


def test_esterno_vede_form_di_scrittura(supervisore_setup) -> None:
    # Controllo di non-regressione: il gating non nasconde i form a tutti,
    # solo al supervisore.
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia-a@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/700")
    assert resp.status_code == 200
    assert 'action="/portale/pratiche/700/note"' in resp.text
    assert 'action="/portale/pratiche/700/eventi"' in resp.text
    assert 'action="/portale/pratiche/700/stato"' in resp.text
    assert 'action="/portale/pratiche/700/foto"' in resp.text
    assert 'action="/portale/pratiche/700/documenti"' in resp.text


# --------------------------------------------------------------------------- #
#  Route di scrittura — 403 per il supervisore, anche su una pratica visibile
# --------------------------------------------------------------------------- #


def test_supervisore_post_nota_403(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")
    csrf = get_csrf(client, "/portale/pratiche/700")

    resp = client.post(
        "/portale/pratiche/700/note", data={"testo": "ciao", "csrf_token": csrf}
    )
    assert resp.status_code == 403


def test_supervisore_post_evento_403(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")
    csrf = get_csrf(client, "/portale/pratiche/700")

    resp = client.post(
        "/portale/pratiche/700/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": csrf},
    )
    assert resp.status_code == 403


def test_supervisore_post_elimina_evento_403(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")
    csrf = get_csrf(client, "/portale/pratiche/700")

    resp = client.post(
        "/portale/pratiche/700/eventi/1/elimina", data={"csrf_token": csrf}
    )
    assert resp.status_code == 403


def test_supervisore_post_stato_403(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")
    csrf = get_csrf(client, "/portale/pratiche/700")

    resp = client.post(
        "/portale/pratiche/700/stato", data={"stato": "periziata", "csrf_token": csrf}
    )
    assert resp.status_code == 403


def test_supervisore_post_upload_foto_403(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")
    csrf = get_csrf(client, "/portale/pratiche/700")

    resp = client.post(
        "/portale/pratiche/700/foto",
        data={"csrf_token": csrf},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 403


def test_supervisore_post_upload_documento_403(supervisore_setup) -> None:
    _, _, supervisore = supervisore_setup
    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")
    csrf = get_csrf(client, "/portale/pratiche/700")

    resp = client.post(
        "/portale/pratiche/700/documenti",
        data={"csrf_token": csrf},
        files={"files": ("perizia.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 403


def test_supervisore_download_zip_consentito(supervisore_setup, tmp_path: Path) -> None:
    # Il download NON è una modifica — deve restare permesso. Foto vera sul
    # filesystem (non solo bytes finti): senza, build_foto_zip risponde 400
    # "nessuna foto valida" e l'assert sotto sarebbe vero per il motivo
    # sbagliato, come segnalato in review.
    _, _, supervisore = supervisore_setup
    foto_dir = tmp_path / "Pratiche" / "700" / "Pubblici" / "Foto"
    foto_dir.mkdir(parents=True)
    (foto_dir / "danno.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    client = TestClient(app, follow_redirects=False)
    login_as(client, supervisore.email, "password1234")

    resp = client.get("/portale/pratiche/700/foto/zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"


# --------------------------------------------------------------------------- #
#  UI admin — /utenti mostra il nuovo ruolo
# --------------------------------------------------------------------------- #


def test_utente_form_mostra_opzione_supervisore(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    from tests.conftest import login_as_admin

    login_as_admin(client)
    resp = client.get("/utenti/nuovo")
    assert resp.status_code == 200
    assert "Supervisore" in resp.text


def test_utenti_list_mostra_badge_supervisore(authenticated_app) -> None:
    authenticated_app.create(
        email="sup@esempio.it", password="password1234", nome="Super", ruolo="supervisore"
    )
    client = TestClient(app, follow_redirects=False)
    from tests.conftest import login_as_admin

    login_as_admin(client)
    resp = client.get("/utenti")
    assert resp.status_code == 200
    assert "Supervisore" in resp.text
