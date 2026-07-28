"""Test delle notifiche di collaborazione (v3.0 fase 5).

Copre sia le funzioni di basso livello in `integrations/notifier.py`
(`notify_push_nuova_attivita`, `notify_esterno_nuova_attivita` — mai
sollevano, anche se il canale sottostante fallisce) sia il collegamento nelle
route: nota/evento di un esterno assegnato notifica l'admin via push,
nota/evento dell'admin su una pratica assegnata notifica gli esterni via
email, e il widget "Prossimi appuntamenti" appare in home/portale.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.pratica_eventi_repository import PraticaEventiRepository
from lys_workflow_hub.core.wincar_repository import (
    Cliente,
    CompagniaCliente,
    Controparte,
    Pratica,
    Sinistro,
    Veicolo,
)
from lys_workflow_hub.integrations.notifier import (
    notify_esterno_nuova_attivita,
    notify_fcm_nuova_attivita,
    notify_push_nuova_attivita,
    send_fcm_push,
)
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes import get_app_settings, get_repository
from lys_workflow_hub.web.routes_portale import (
    get_assegnazioni_repo,
    get_portale_settings,
    get_wincar_repo,
)
from tests.conftest import get_csrf, login_as, login_as_admin

# --------------------------------------------------------------------------- #
#  notifier.py — funzioni di basso livello
# --------------------------------------------------------------------------- #


def test_notify_push_nuova_attivita_chiama_send_push_se_configurato() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_push") as mock_push:
        mock_push.return_value = (True, "")
        notify_push_nuova_attivita(
            ntfy_server="https://ntfy.sh",
            ntfy_topic="topic-segreto",
            titolo="Nuova nota",
            messaggio="ciao",
            click_url="https://hub.lysauto.it/pratiche/766",
        )
        mock_push.assert_called_once()
        assert mock_push.call_args.kwargs["topic"] == "topic-segreto"


def test_notify_push_nuova_attivita_skip_senza_topic() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_push") as mock_push:
        notify_push_nuova_attivita(
            ntfy_server="https://ntfy.sh", ntfy_topic="", titolo="x", messaggio="y"
        )
        mock_push.assert_not_called()


def test_notify_push_nuova_attivita_non_solleva_se_push_fallisce() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_push") as mock_push:
        mock_push.side_effect = RuntimeError("boom")
        notify_push_nuova_attivita(
            ntfy_server="https://ntfy.sh", ntfy_topic="t", titolo="x", messaggio="y"
        )  # non deve sollevare


def test_notify_fcm_nuova_attivita_chiama_send_fcm_push_se_configurato() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_fcm_push") as mock_fcm:
        mock_fcm.return_value = (True, "")
        notify_fcm_nuova_attivita(
            fcm_project_id="lys-workflow-hub",
            fcm_credentials_path="/tmp/fake-service-account.json",
            fcm_token="device-token-xyz",
            titolo="Nuova nota",
            messaggio="ciao",
        )
        mock_fcm.assert_called_once()
        assert mock_fcm.call_args.kwargs["token"] == "device-token-xyz"


def test_notify_fcm_nuova_attivita_skip_senza_token() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_fcm_push") as mock_fcm:
        notify_fcm_nuova_attivita(
            fcm_project_id="lys-workflow-hub",
            fcm_credentials_path="/tmp/fake-service-account.json",
            fcm_token="",
            titolo="x",
            messaggio="y",
        )
        mock_fcm.assert_not_called()


def test_notify_fcm_nuova_attivita_non_solleva_se_push_fallisce() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_fcm_push") as mock_fcm:
        mock_fcm.side_effect = RuntimeError("boom")
        notify_fcm_nuova_attivita(
            fcm_project_id="lys-workflow-hub",
            fcm_credentials_path="/tmp/fake-service-account.json",
            fcm_token="t",
            titolo="x",
            messaggio="y",
        )  # non deve sollevare


def test_send_fcm_push_richiede_priorita_alta_android() -> None:
    # Senza android.priority=high, FCM consegna a priorità "normal" e Android
    # può ritardare la notifica di minuti sotto Doze/App Standby — bug reale
    # segnalato dall'utente (notifica arrivata 5 minuti dopo, in modo non
    # riproducibile, nessun errore lato server). L'endpoint FCM HTTP v1
    # ignora silenziosamente i campi sconosciuti/mal posizionati: un domani
    # spostare o rinominare questa chiave passerebbe il controllo HTTP 2xx
    # senza errori, riproducendo lo stesso sintomo. Questo test blocca quella
    # regressione silenziosa.
    fake_creds = MagicMock()
    fake_creds.token = "fake-access-token"
    fake_response = MagicMock()
    fake_response.status_code = 200

    with (
        patch(
            "google.oauth2.service_account.Credentials.from_service_account_file",
            return_value=fake_creds,
        ),
        patch("requests.post", return_value=fake_response) as mock_post,
    ):
        ok, err = send_fcm_push(
            project_id="lys-workflow-hub",
            credentials_path="/tmp/fake-service-account.json",
            token="device-token-xyz",
            title="Nuova nota",
            message="ciao",
        )

    assert ok is True
    assert err == ""
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["message"]["android"]["priority"] == "high"


def test_notify_esterno_nuova_attivita_chiama_send_email_se_configurato() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_email") as mock_mail:
        mock_mail.return_value = (True, "")
        notify_esterno_nuova_attivita(
            smtp_host="mail.tophost.it", smtp_port=587, smtp_user="u", smtp_password="p",
            smtp_sender="u", recipient="agenzia@esempio.it", subject="Nuova nota",
            body_text="ciao",
        )
        mock_mail.assert_called_once()
        assert mock_mail.call_args.kwargs["recipient"] == "agenzia@esempio.it"


def test_notify_esterno_nuova_attivita_skip_senza_recipient() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_email") as mock_mail:
        notify_esterno_nuova_attivita(
            smtp_host="mail.tophost.it", smtp_port=587, smtp_user="u", smtp_password="p",
            smtp_sender="u", recipient="", subject="x", body_text="y",
        )
        mock_mail.assert_not_called()


def test_notify_esterno_nuova_attivita_non_solleva_se_email_fallisce() -> None:
    with patch("lys_workflow_hub.integrations.notifier.send_email") as mock_mail:
        mock_mail.side_effect = RuntimeError("boom")
        notify_esterno_nuova_attivita(
            smtp_host="mail.tophost.it", smtp_port=587, smtp_user="u", smtp_password="p",
            smtp_sender="u", recipient="a@b.it", subject="x", body_text="y",
        )  # non deve sollevare


# --------------------------------------------------------------------------- #
#  Collegamento nelle route
# --------------------------------------------------------------------------- #


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
    settings = Settings(
        wincar_archivio=tmp_path,
        app_db_path=tmp_path / "app.db",
        smtp_host="mail.tophost.it",
        smtp_port=587,
        smtp_user="u",
        smtp_password="p",
        smtp_from="u@lysauto.it",
    )

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
    settings = Settings(
        wincar_archivio=tmp_path,
        app_db_path=tmp_path / "app.db",
        ntfy_server="https://ntfy.sh",
        ntfy_topic="topic-segreto",
    )

    app.dependency_overrides[get_assegnazioni_repo] = lambda: assegnazioni_repo
    app.dependency_overrides[get_wincar_repo] = lambda: wincar_repo
    app.dependency_overrides[get_portale_settings] = lambda: settings
    try:
        yield assegnazioni_repo, settings
    finally:
        app.dependency_overrides.pop(get_assegnazioni_repo, None)
        app.dependency_overrides.pop(get_wincar_repo, None)
        app.dependency_overrides.pop(get_portale_settings, None)


def test_admin_aggiunge_nota_notifica_esterno_assegnato(admin_client, authenticated_app) -> None:
    client, _ = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=admin_client[1].app_db_path)
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_notify:
        resp = client.post(
            "/pratiche/766/note",
            data={"testo": "servono foto lavorazione", "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_notify.assert_called_once()
        assert mock_notify.call_args.kwargs["recipient"] == "agenzia@esempio.it"
        assert "766" in mock_notify.call_args.kwargs["subject"]


def test_admin_aggiunge_nota_non_notifica_se_nessun_assegnato(admin_client) -> None:
    client, _ = admin_client
    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_notify:
        resp = client.post(
            "/pratiche/766/note",
            data={"testo": "nota interna", "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_notify.assert_not_called()


def test_admin_carica_foto_notifica_esterno_assegnato(admin_client, authenticated_app) -> None:
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_notify:
        resp = client.post(
            "/pratiche/766/foto",
            data={"csrf_token": get_csrf(client, "/pratiche/766")},
            files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
        )
        assert resp.status_code == 303
        mock_notify.assert_called_once()
        assert mock_notify.call_args.kwargs["recipient"] == "agenzia@esempio.it"
        assert "766" in mock_notify.call_args.kwargs["subject"]


def test_admin_carica_documento_notifica_esterno_assegnato(admin_client, authenticated_app) -> None:
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_notify:
        resp = client.post(
            "/pratiche/766/documenti",
            data={"csrf_token": get_csrf(client, "/pratiche/766")},
            files={"files": ("preventivo.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert resp.status_code == 303
        mock_notify.assert_called_once()
        assert mock_notify.call_args.kwargs["recipient"] == "agenzia@esempio.it"
        assert "766" in mock_notify.call_args.kwargs["subject"]


def test_admin_carica_foto_non_notifica_se_nessun_assegnato(admin_client) -> None:
    client, _ = admin_client
    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_notify:
        resp = client.post(
            "/pratiche/766/foto",
            data={"csrf_token": get_csrf(client, "/pratiche/766")},
            files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
        )
        assert resp.status_code == 303
        mock_notify.assert_not_called()


def test_esterno_aggiunge_nota_notifica_admin(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    token = get_csrf(client, "/portale/pratiche/766")
    with patch("lys_workflow_hub.web.routes_portale.notify_push_nuova_attivita") as mock_notify:
        resp = client.post(
            "/portale/pratiche/766/note",
            data={"testo": "preso app.to con perito", "csrf_token": token},
        )
        assert resp.status_code == 303
        mock_notify.assert_called_once()
        assert "766" in mock_notify.call_args.kwargs["titolo"]
        assert "Agenzia" in mock_notify.call_args.kwargs["messaggio"]


def test_esterno_aggiunge_evento_notifica_admin(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    token = get_csrf(client, "/portale/pratiche/766")
    with patch("lys_workflow_hub.web.routes_portale.notify_push_nuova_attivita") as mock_notify:
        resp = client.post(
            "/portale/pratiche/766/eventi",
            data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
        )
        assert resp.status_code == 303
        mock_notify.assert_called_once()


def test_esterno_cambia_stato_notifica_admin(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    token = get_csrf(client, "/portale/pratiche/766")
    with patch("lys_workflow_hub.web.routes_portale.notify_push_nuova_attivita") as mock_notify:
        resp = client.post(
            "/portale/pratiche/766/stato",
            data={"stato": "in_liquidazione", "csrf_token": token},
        )
        assert resp.status_code == 303
        mock_notify.assert_called_once()
        assert "766" in mock_notify.call_args.kwargs["titolo"]
        assert "Agenzia" in mock_notify.call_args.kwargs["messaggio"]


def test_esterno_carica_foto_notifica_admin(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    token = get_csrf(client, "/portale/pratiche/766")
    with patch("lys_workflow_hub.web.routes_portale.notify_push_nuova_attivita") as mock_notify:
        resp = client.post(
            "/portale/pratiche/766/foto",
            data={"csrf_token": token},
            files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
        )
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portale/pratiche/766?upload_ok=1")
        mock_notify.assert_called_once()
        assert "766" in mock_notify.call_args.kwargs["titolo"]
        assert "Agenzia" in mock_notify.call_args.kwargs["messaggio"]


def test_esterno_carica_documento_notifica_admin(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    token = get_csrf(client, "/portale/pratiche/766")
    with patch("lys_workflow_hub.web.routes_portale.notify_push_nuova_attivita") as mock_notify:
        resp = client.post(
            "/portale/pratiche/766/documenti",
            data={"csrf_token": token},
            files={"files": ("doc.pdf", b"%PDF-fake", "application/pdf")},
        )
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portale/pratiche/766?upload_ok=1")
        mock_notify.assert_called_once()
        assert "766" in mock_notify.call_args.kwargs["titolo"]
        assert "Agenzia" in mock_notify.call_args.kwargs["messaggio"]


def test_esterno_carica_file_rifiutato_non_notifica_admin(authenticated_app, portale_setup) -> None:
    """Un upload interamente respinto (estensione non valida) non deve
    notificare l'admin — `_notifica_admin` è dentro `if salvati:`."""
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    token = get_csrf(client, "/portale/pratiche/766")
    with patch("lys_workflow_hub.web.routes_portale.notify_push_nuova_attivita") as mock_notify:
        resp = client.post(
            "/portale/pratiche/766/documenti",
            data={"csrf_token": token},
            files={"files": ("virus.exe", b"MZ-fake-binary", "application/octet-stream")},
        )
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portale/pratiche/766?upload_ok=0")
        mock_notify.assert_not_called()


# --------------------------------------------------------------------------- #
#  Widget "Prossimi appuntamenti"
# --------------------------------------------------------------------------- #


def test_home_mostra_prossimi_appuntamenti(admin_client) -> None:
    client, settings = admin_client
    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    eventi_repo.add(766, "Perizia", date.today() + timedelta(days=1), 1, "Admin")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Prossimi appuntamenti" in resp.text
    assert "Perizia" in resp.text


def test_home_prossimi_appuntamenti_mostra_cliente_e_targa(admin_client) -> None:
    client, settings = admin_client
    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    eventi_repo.add(766, "Perizia", date.today() + timedelta(days=1), 1, "Admin")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "ROSSI MARIO" in resp.text  # cliente da _sample_pratica()
    assert "AB123CD" in resp.text  # targa da _sample_pratica()
    assert "Pratica nr." in resp.text


def test_home_prossimi_appuntamenti_evento_con_pratica_irraggiungibile_non_rompe_widget(
    admin_client,
) -> None:
    """Un fallimento WinCar su UN evento non deve far sparire l'intero
    widget (né gli altri eventi): _arricchisci_eventi_con_pratica tollera
    l'errore per singolo evento, non lo propaga."""
    client, settings = admin_client
    repo = app.dependency_overrides[get_repository]()
    repo.get_pratica.side_effect = lambda numero: (
        (_ for _ in ()).throw(RuntimeError("WinCar irraggiungibile"))
        if numero == 766
        else _sample_pratica(numero)
    )

    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    eventi_repo.add(766, "Fallisce", date.today() + timedelta(days=1), 1, "Admin")
    eventi_repo.add(999, "Va bene", date.today() + timedelta(days=1), 1, "Admin")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Prossimi appuntamenti" in resp.text
    assert "Fallisce" in resp.text  # evento presente comunque, solo senza cliente/targa
    assert "Va bene" in resp.text


def test_home_senza_eventi_non_mostra_widget(admin_client) -> None:
    client, _ = admin_client
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Prossimi appuntamenti" not in resp.text


def test_portale_mostra_prossimi_appuntamenti_solo_assegnati(
    authenticated_app, portale_setup
) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    eventi_repo.add(766, "Perizia mia", date.today() + timedelta(days=1), 1, "Admin")
    eventi_repo.add(999, "Perizia altrui", date.today() + timedelta(days=1), 1, "Admin")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale")
    assert resp.status_code == 200
    assert "Perizia mia" in resp.text
    assert "Perizia altrui" not in resp.text
