"""Test delle note/calendario condivisi su pratica (v3.0 fase 4).

Copre sia il lato admin (/pratiche/{numero}/note, /eventi) sia il lato
esterno (/portale/pratiche/{numero}/note, /eventi), incluso il controllo di
accesso (esterno non assegnato -> 404) e la protezione IDOR sulla
cancellazione di un evento di un'altra pratica.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.pratica_eventi_repository import PraticaEventiRepository
from lys_workflow_hub.core.pratica_stato_repository import PraticaStatoRepository
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
from lys_workflow_hub.web.routes_portale import (
    get_assegnazioni_repo,
    get_portale_settings,
    get_wincar_repo,
)
from tests.conftest import get_csrf, login_as, login_as_admin


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
def admin_client(tmp_path: Path, authenticated_app):
    repo = MagicMock()
    repo.get_pratica.return_value = _sample_pratica()
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")

    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_app_settings] = lambda: settings
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)
    try:
        yield client, settings
    finally:
        app.dependency_overrides.pop(get_repository, None)
        app.dependency_overrides.pop(get_app_settings, None)


@pytest.fixture
def portale_setup(tmp_path: Path, authenticated_app):
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=tmp_path / "app.db")
    wincar_repo = MagicMock()
    wincar_repo.get_pratica.side_effect = lambda n: _sample_pratica(n) if n == 766 else None
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")

    app.dependency_overrides[get_assegnazioni_repo] = lambda: assegnazioni_repo
    app.dependency_overrides[get_wincar_repo] = lambda: wincar_repo
    app.dependency_overrides[get_portale_settings] = lambda: settings
    try:
        yield assegnazioni_repo, settings
    finally:
        app.dependency_overrides.pop(get_assegnazioni_repo, None)
        app.dependency_overrides.pop(get_wincar_repo, None)
        app.dependency_overrides.pop(get_portale_settings, None)


# --------------------------------------------------------------------------- #
#  Lato admin
# --------------------------------------------------------------------------- #


def test_admin_aggiunge_nota_e_evento(admin_client) -> None:
    client, _ = admin_client
    token = get_csrf(client, "/pratiche/766")

    resp = client.post(
        "/pratiche/766/note", data={"testo": "servono foto lavorazione", "csrf_token": token}
    )
    assert resp.status_code == 303

    resp = client.post(
        "/pratiche/766/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
    )
    assert resp.status_code == 303

    resp = client.get("/pratiche/766")
    assert "servono foto lavorazione" in resp.text
    assert "Perizia" in resp.text
    assert "05/08/2026" in resp.text


def test_admin_elimina_evento(admin_client) -> None:
    client, _ = admin_client
    token = get_csrf(client, "/pratiche/766")
    client.post(
        "/pratiche/766/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
    )
    resp = client.get("/pratiche/766")
    assert "Perizia" in resp.text

    match = re.search(r'/pratiche/766/eventi/(\d+)/elimina', resp.text)
    assert match, "azione di eliminazione evento non trovata"
    resp = client.post(match.group(0), data={"csrf_token": token})
    assert resp.status_code == 303

    resp = client.get("/pratiche/766")
    assert "Nessun evento in calendario" in resp.text


# --------------------------------------------------------------------------- #
#  Lato portale (esterno)
# --------------------------------------------------------------------------- #


def test_portale_detail_richiede_assegnazione(authenticated_app, portale_setup) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 404


def test_portale_detail_e_collaborazione_utente_assegnato(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert "ROSSI MARIO" in resp.text
    token = get_csrf(client, "/portale/pratiche/766")

    resp = client.post(
        "/portale/pratiche/766/note",
        data={"testo": "preso app.to con perito", "csrf_token": token},
    )
    assert resp.status_code == 303

    resp = client.post(
        "/portale/pratiche/766/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
    )
    assert resp.status_code == 303

    resp = client.get("/portale/pratiche/766")
    assert "preso app.to con perito" in resp.text
    assert "Perizia" in resp.text


def test_portale_mostra_stato_default_aperta(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert "Aperta" in resp.text
    assert "Aggiorna stato" in resp.text


def test_portale_puo_cambiare_stato_pratica_assegnata(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={
            "stato": "in_gestione",
            "note": "preso in carico",
            "csrf_token": get_csrf(client, "/portale/pratiche/766"),
        },
    )
    assert resp.status_code == 303

    stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
    stato = stato_repo.get_stato(766)
    assert stato.stato == "in_gestione"
    assert stato.changed_by == "Agenzia"
    assert stato.note == "preso in carico"


def test_portale_dropdown_stato_include_periziata(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert '<option value="periziata"' in resp.text
    assert "Periziata" in resp.text


def test_portale_puo_impostare_stato_periziata(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={"stato": "periziata", "csrf_token": get_csrf(client, "/portale/pratiche/766")},
    )
    assert resp.status_code == 303

    stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
    assert stato_repo.get_stato(766).stato == "periziata"


def test_portale_cambia_stato_rifiuta_valore_non_valido(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={"stato": "non-esiste", "csrf_token": get_csrf(client, "/portale/pratiche/766")},
    )
    assert resp.status_code == 400


def test_portale_non_assegnato_non_puo_cambiare_stato(authenticated_app, portale_setup) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={"stato": "chiusa", "csrf_token": get_csrf(client, "/portale")},
    )
    assert resp.status_code == 404


def test_portale_non_assegnato_non_puo_scrivere_note(authenticated_app, portale_setup) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/note",
        data={"testo": "non dovrei poterlo fare", "csrf_token": get_csrf(client, "/portale")},
    )
    assert resp.status_code == 404


def test_portale_foto_pratica_serve_file_reale_non_403(authenticated_app, portale_setup) -> None:
    """Regressione: gli URL di foto/documenti nella pagina portale devono
    puntare a `/portale/pratiche/{numero}/file` (verifica assegnazione), non
    a `/pratiche/{numero}/file` (admin-only) — altrimenti un esterno assegnato
    riceve 403 aprendo una foto della propria pratica."""
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    foto_dir.mkdir(parents=True)
    (foto_dir / "danno.jpg").write_bytes(b"fake-jpeg-bytes")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert "/portale/pratiche/766/file?path=" in resp.text
    assert 'src="/pratiche/766/file?path=' not in resp.text  # non l'URL admin-only

    match = re.search(r'src="(/portale/pratiche/766/file\?path=[^"]+)"', resp.text)
    assert match, "URL foto non trovato in pagina"

    resp = client.get(match.group(1))
    assert resp.status_code == 200
    assert resp.content == b"fake-jpeg-bytes"


def test_portale_non_puo_eliminare_evento_di_altra_pratica(authenticated_app, portale_setup) -> None:
    """IDOR: un esterno assegnato alla pratica 766 non deve poter cancellare
    un evento che appartiene alla pratica 999, anche indovinandone l'id."""
    assegnazioni_repo, settings = portale_setup
    esterno_a = authenticated_app.create(
        email="a@esempio.it", password="password1234", nome="A", ruolo="esterno"
    )
    esterno_b = authenticated_app.create(
        email="b@esempio.it", password="password1234", nome="B", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno_a.id, assegnato_da=1)
    assegnazioni_repo.assegna(999, esterno_b.id, assegnato_da=1)

    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    evento_999 = eventi_repo.add(999, "Perizia riservata", date(2026, 8, 5), esterno_b.id, "B")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "a@esempio.it", "password1234")

    # 766 è assegnata ad A: la route risponde 303 (non 403/404 sulla richiesta
    # in sé, perché A ha accesso a 766), ma l'evento di 999 resta intatto.
    resp = client.post(
        f"/portale/pratiche/766/eventi/{evento_999.id}/elimina",
        data={"csrf_token": get_csrf(client, "/portale/pratiche/766")},
    )
    assert resp.status_code == 303
    assert eventi_repo.list_per_pratica(999) != []
