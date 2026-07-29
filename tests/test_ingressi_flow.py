"""Test di integrazione del flusso ingressi officina: operatore crea +
carica documenti, admin collega a una pratica WinCar (mockata) e i file
finiscono nella cartella pratica reale, staging ripulito."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.config import Settings
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
from lys_workflow_hub.web.routes_ingressi import (
    get_ingressi_settings as get_ingressi_settings_admin,
)
from lys_workflow_hub.web.routes_ingressi import get_wincar_repo
from lys_workflow_hub.web.routes_operatore import (
    get_operatore_settings as get_operatore_settings_op,
)
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, get_csrf, login_as

OPERATORE_EMAIL = "operatore@test.local"
OPERATORE_PASSWORD = "test-password-1234"


def _sample_pratica(numero: int) -> Pratica:
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
def client_with_mocks(tmp_path: Path, authenticated_app: UtentiRepository):
    authenticated_app.create(
        email=OPERATORE_EMAIL, password=OPERATORE_PASSWORD, nome="Operatore Test", ruolo="operatore"
    )
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")
    wincar_repo = MagicMock()
    wincar_repo.get_pratica.return_value = _sample_pratica(766)

    app.dependency_overrides[get_operatore_settings_op] = lambda: settings
    app.dependency_overrides[get_ingressi_settings_admin] = lambda: settings
    app.dependency_overrides[get_wincar_repo] = lambda: wincar_repo
    try:
        client = TestClient(app)
        yield client, wincar_repo, settings, tmp_path
    finally:
        app.dependency_overrides.pop(get_operatore_settings_op, None)
        app.dependency_overrides.pop(get_ingressi_settings_admin, None)
        app.dependency_overrides.pop(get_wincar_repo, None)


def test_operatore_non_puo_vedere_portale_admin(client_with_mocks):
    client, *_ = client_with_mocks
    login_as(client, OPERATORE_EMAIL, OPERATORE_PASSWORD)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/operatore"


def test_esterno_non_operatore_riceve_403_su_operatore(client_with_mocks):
    client, *_ = client_with_mocks
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    csrf = get_csrf(client, "/")
    response = client.post(
        "/operatore/ingressi",
        data={"cliente_nominativo": "Mario Rossi", "csrf_token": csrf},
    )
    assert response.status_code == 403


def test_flusso_completo_crea_upload_collega(client_with_mocks):
    client, wincar_repo, settings, tmp_path = client_with_mocks
    login_as(client, OPERATORE_EMAIL, OPERATORE_PASSWORD)

    csrf = get_csrf(client, "/operatore")
    resp = client.post(
        "/operatore/ingressi",
        data={
            "cliente_nominativo": "Mario Rossi",
            "targa": "AB123CD",
            "note": "urto posteriore",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    ingresso_url = resp.headers["location"]
    ingresso_id = int(ingresso_url.rstrip("/").rsplit("/", 1)[-1])

    csrf = get_csrf(client, ingresso_url)
    resp = client.post(
        f"/operatore/ingressi/{ingresso_id}/upload",
        data={"tipo": "cid", "csrf_token": csrf},
        files={"files": ("cid.pdf", b"%PDF-1.4 fake", "application/pdf")},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    csrf = get_csrf(client, ingresso_url)
    resp = client.post(
        f"/operatore/ingressi/{ingresso_id}/upload",
        data={"tipo": "foto_danno", "csrf_token": csrf},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Staging popolato, fuori da Pratiche/.
    staging_dir = tmp_path / "IngressiOfficina" / str(ingresso_id)
    assert staging_dir.exists()
    assert not (tmp_path / "Pratiche").exists()

    client.post("/logout", data={"csrf_token": get_csrf(client, "/operatore")})
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    csrf = get_csrf(client, f"/ingressi/{ingresso_id}")
    resp = client.post(
        f"/ingressi/{ingresso_id}/collega",
        data={"numero_pratica": "766", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/pratiche/766"

    # I file sono finiti nella cartella pratica reale, categorizzati bene.
    allegati_dir = tmp_path / "Pratiche" / "766" / "Pubblici" / "Allegati"
    foto_dir = tmp_path / "Pratiche" / "766" / "Pubblici" / "Foto"
    assert list(allegati_dir.glob("cid_*.pdf"))
    assert list(foto_dir.glob("danno_*.jpg"))

    # Staging ripulito dopo il collegamento.
    assert not staging_dir.exists()


def test_collega_due_volte_seconda_fallisce_e_niente_duplicati(client_with_mocks):
    """Regressione: prima del fix, il loop di copia file girava PRIMA della
    transizione di stato atomica — un secondo tentativo (retry dopo errore
    o doppio submit) ricopiava gli stessi file con un nome nuovo
    (`save_upload` non sovrascrive mai), duplicandoli silenziosamente nella
    cartella pubblica della pratica. Ora la transizione di stato avviene
    PRIMA della copia: un secondo tentativo — qui sequenziale, quindi
    intercettato dal controllo `is_in_attesa` (400) più a monte del guard
    atomico (409, che scatta solo su una vera race concorrente) — non deve
    MAI toccare il filesystem una seconda volta, qualunque sia lo status
    code esatto."""
    client, wincar_repo, settings, tmp_path = client_with_mocks
    login_as(client, OPERATORE_EMAIL, OPERATORE_PASSWORD)

    csrf = get_csrf(client, "/operatore")
    resp = client.post(
        "/operatore/ingressi",
        data={"cliente_nominativo": "Mario Rossi", "csrf_token": csrf},
        follow_redirects=False,
    )
    ingresso_url = resp.headers["location"]
    ingresso_id = int(ingresso_url.rstrip("/").rsplit("/", 1)[-1])

    csrf = get_csrf(client, ingresso_url)
    client.post(
        f"/operatore/ingressi/{ingresso_id}/upload",
        data={"tipo": "cid", "csrf_token": csrf},
        files={"files": ("cid.pdf", b"%PDF-1.4 fake", "application/pdf")},
        follow_redirects=False,
    )

    client.post("/logout", data={"csrf_token": get_csrf(client, "/operatore")})
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    csrf = get_csrf(client, f"/ingressi/{ingresso_id}")
    resp1 = client.post(
        f"/ingressi/{ingresso_id}/collega",
        data={"numero_pratica": "766", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp1.status_code == 303

    # Stesso token CSRF di sessione riusabile su più richieste: simula un
    # secondo tentativo (doppio click, retry) sullo stesso ingresso.
    resp2 = client.post(
        f"/ingressi/{ingresso_id}/collega",
        data={"numero_pratica": "766", "csrf_token": csrf},
    )
    assert resp2.status_code in (400, 409)

    allegati_dir = tmp_path / "Pratiche" / "766" / "Pubblici" / "Allegati"
    assert len(list(allegati_dir.glob("cid_*.pdf"))) == 1


def test_collega_con_numero_pratica_inesistente_400(client_with_mocks):
    client, wincar_repo, settings, tmp_path = client_with_mocks
    login_as(client, OPERATORE_EMAIL, OPERATORE_PASSWORD)
    csrf = get_csrf(client, "/operatore")
    resp = client.post(
        "/operatore/ingressi",
        data={"cliente_nominativo": "Mario Rossi", "csrf_token": csrf},
        follow_redirects=False,
    )
    ingresso_id = int(resp.headers["location"].rstrip("/").rsplit("/", 1)[-1])

    client.post("/logout", data={"csrf_token": get_csrf(client, "/operatore")})
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    wincar_repo.get_pratica.return_value = None
    csrf = get_csrf(client, f"/ingressi/{ingresso_id}")
    resp = client.post(
        f"/ingressi/{ingresso_id}/collega",
        data={"numero_pratica": "9999", "csrf_token": csrf},
    )
    assert resp.status_code == 400
