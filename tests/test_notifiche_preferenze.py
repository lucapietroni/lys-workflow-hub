"""Preferenze di notifica self-service per utenti esterni (v3.0 fase 5,
parte D): pagina /portale/impostazioni + gating in
`routes._notifica_esterni_assegnati`.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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
from lys_workflow_hub.web.routes import get_app_settings, get_repository
from tests.conftest import get_csrf, login_as, login_as_admin


def _sample_pratica(numero: int = 766) -> Pratica:
    return Pratica(
        numero=numero,
        data_creazione=None,
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


# --------------------------------------------------------------------------- #
#  UtentiRepository.set_notifiche
# --------------------------------------------------------------------------- #


def test_set_notifiche_salva_preferenze(utenti_repo: UtentiRepository) -> None:
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    utenti_repo.set_notifiche(
        esterno.id,
        notify_email_enabled=False,
        notify_push_enabled=True,
        ntfy_topic="lys-agenzia-9f3a",
    )
    aggiornato = utenti_repo.get(esterno.id)
    assert aggiornato.notify_email_enabled is False
    assert aggiornato.notify_push_enabled is True
    assert aggiornato.ntfy_topic == "lys-agenzia-9f3a"


def test_set_notifiche_rifiuta_push_senza_topic(utenti_repo: UtentiRepository) -> None:
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    with pytest.raises(ValueError):
        utenti_repo.set_notifiche(
            esterno.id, notify_email_enabled=True, notify_push_enabled=True, ntfy_topic=""
        )


def test_set_notifiche_rifiuta_topic_con_caratteri_non_validi(
    utenti_repo: UtentiRepository,
) -> None:
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    with pytest.raises(ValueError):
        utenti_repo.set_notifiche(
            esterno.id,
            notify_email_enabled=True,
            notify_push_enabled=True,
            ntfy_topic="lys agenzia rossi",
        )


def test_nuovo_utente_ha_email_abilitata_e_push_disabilitato_di_default(
    utenti_repo: UtentiRepository,
) -> None:
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    assert esterno.notify_email_enabled is True
    assert esterno.notify_push_enabled is False
    assert esterno.ntfy_topic == ""


# --------------------------------------------------------------------------- #
#  GET/POST /portale/impostazioni
# --------------------------------------------------------------------------- #


def test_portale_impostazioni_richiede_login() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/portale/impostazioni")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_portale_impostazioni_get_mostra_preferenze_correnti(authenticated_app) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/impostazioni")
    assert resp.status_code == 200
    assert "agenzia@esempio.it" in resp.text


def test_portale_impostazioni_post_salva_e_persiste(authenticated_app) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/impostazioni",
        data={
            "notify_push_enabled": "on",
            "ntfy_topic": "lys-agenzia-9f3a",
            "csrf_token": get_csrf(client, "/portale/impostazioni"),
        },
    )
    assert resp.status_code == 303

    aggiornato = authenticated_app.get(esterno.id)
    assert aggiornato.notify_email_enabled is False  # checkbox non inviata = non spuntata
    assert aggiornato.notify_push_enabled is True
    assert aggiornato.ntfy_topic == "lys-agenzia-9f3a"


def test_portale_impostazioni_post_push_senza_topic_mostra_errore(authenticated_app) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/impostazioni",
        data={
            "notify_push_enabled": "on",
            "csrf_token": get_csrf(client, "/portale/impostazioni"),
        },
    )
    assert resp.status_code == 400
    assert "topic" in resp.text.lower()

    invariato = authenticated_app.get(esterno.id)
    assert invariato.notify_push_enabled is False


# --------------------------------------------------------------------------- #
#  Gating in _notifica_esterni_assegnati (routes.py)
# --------------------------------------------------------------------------- #


def test_admin_nota_non_manda_email_se_esterno_ha_disattivato(
    admin_client, authenticated_app
) -> None:
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_notifiche(
        esterno.id, notify_email_enabled=False, notify_push_enabled=False, ntfy_topic=""
    )
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_email:
        resp = client.post(
            "/pratiche/766/note",
            data={"testo": "aggiornamento", "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_email.assert_not_called()


def test_admin_nota_manda_push_se_esterno_ha_attivato(admin_client, authenticated_app) -> None:
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_notifiche(
        esterno.id,
        notify_email_enabled=True,
        notify_push_enabled=True,
        ntfy_topic="lys-agenzia-9f3a",
    )
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    with patch("lys_workflow_hub.web.routes.notify_push_nuova_attivita") as mock_push:
        resp = client.post(
            "/pratiche/766/note",
            data={"testo": "aggiornamento", "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_push.assert_called_once()
        assert mock_push.call_args.kwargs["ntfy_topic"] == "lys-agenzia-9f3a"


# --------------------------------------------------------------------------- #
#  Notifica su nuova assegnazione (POST /pratiche/{numero}/assegna, v3.0 fase 6)
# --------------------------------------------------------------------------- #


def test_assegna_pratica_manda_push_secondo_preferenze(admin_client, authenticated_app) -> None:
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_notifiche(
        esterno.id,
        notify_email_enabled=True,
        notify_push_enabled=True,
        ntfy_topic="lys-agenzia-9f3a",
    )

    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_email, \
         patch("lys_workflow_hub.web.routes.notify_push_nuova_attivita") as mock_push:
        resp = client.post(
            "/pratiche/766/assegna",
            data={"utente_id": esterno.id, "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_email.assert_called_once()
        assert mock_email.call_args.kwargs["recipient"] == "agenzia@esempio.it"
        mock_push.assert_called_once()
        assert mock_push.call_args.kwargs["ntfy_topic"] == "lys-agenzia-9f3a"
        assert "766" in mock_push.call_args.kwargs["titolo"]


def test_assegna_pratica_non_manda_se_preferenze_disattivate(
    admin_client, authenticated_app
) -> None:
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_notifiche(
        esterno.id, notify_email_enabled=False, notify_push_enabled=False, ntfy_topic=""
    )

    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_email, \
         patch("lys_workflow_hub.web.routes.notify_push_nuova_attivita") as mock_push:
        resp = client.post(
            "/pratiche/766/assegna",
            data={"utente_id": esterno.id, "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_email.assert_not_called()
        mock_push.assert_not_called()


def test_assegna_pratica_due_volte_non_duplica_notifica(
    admin_client, authenticated_app
) -> None:
    """Riassegnare lo stesso utente (idempotente in PraticaAssegnazioniRepository)
    non deve rimandare la notifica la seconda volta."""
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_notifiche(
        esterno.id,
        notify_email_enabled=True,
        notify_push_enabled=True,
        ntfy_topic="lys-agenzia-9f3a",
    )

    with patch("lys_workflow_hub.web.routes.notify_esterno_nuova_attivita") as mock_email:
        client.post(
            "/pratiche/766/assegna",
            data={"utente_id": esterno.id, "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        client.post(
            "/pratiche/766/assegna",
            data={"utente_id": esterno.id, "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        mock_email.assert_called_once()


# --------------------------------------------------------------------------- #
#  UtentiRepository.set_fcm_token
# --------------------------------------------------------------------------- #


def test_set_fcm_token_salva_e_sovrascrive(utenti_repo: UtentiRepository) -> None:
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    assert esterno.fcm_token == ""

    utenti_repo.set_fcm_token(esterno.id, "token-device-1")
    assert utenti_repo.get(esterno.id).fcm_token == "token-device-1"

    utenti_repo.set_fcm_token(esterno.id, "token-device-2")
    assert utenti_repo.get(esterno.id).fcm_token == "token-device-2"


def test_set_fcm_token_stringa_vuota_cancella(utenti_repo: UtentiRepository) -> None:
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    utenti_repo.set_fcm_token(esterno.id, "token-device-1")
    utenti_repo.set_fcm_token(esterno.id, "")
    assert utenti_repo.get(esterno.id).fcm_token == ""


def test_set_fcm_token_toglie_il_token_a_chi_lo_aveva_prima(utenti_repo: UtentiRepository) -> None:
    """Regressione: stesso telefono usato prima per un account esterno di
    test, poi per il login admin — senza deduplica cross-utente entrambi
    restavano registrati con lo stesso token fisico per sempre, e ciascuno
    riceveva anche le push destinate all'altro (bug reale osservato:
    l'admin vedeva le notifiche delle proprie stesse note)."""
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    admin = utenti_repo.create(
        email="admin2@esempio.it", password="password1234", ruolo="admin"
    )
    utenti_repo.set_fcm_token(esterno.id, "stesso-telefono-token")
    assert utenti_repo.get(esterno.id).fcm_token == "stesso-telefono-token"

    utenti_repo.set_fcm_token(admin.id, "stesso-telefono-token")

    assert utenti_repo.get(admin.id).fcm_token == "stesso-telefono-token"
    assert utenti_repo.get(esterno.id).fcm_token == ""


def test_set_fcm_token_web_toglie_il_token_a_chi_lo_aveva_prima(
    utenti_repo: UtentiRepository,
) -> None:
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    admin = utenti_repo.create(
        email="admin2@esempio.it", password="password1234", ruolo="admin"
    )
    utenti_repo.set_fcm_token_web(esterno.id, "stesso-browser-token")
    utenti_repo.set_fcm_token_web(admin.id, "stesso-browser-token")

    assert utenti_repo.get(admin.id).fcm_token_web == "stesso-browser-token"
    assert utenti_repo.get(esterno.id).fcm_token_web == ""


def test_set_fcm_token_stringa_vuota_non_ruba_token_da_altri(
    utenti_repo: UtentiRepository,
) -> None:
    """Cancellare il PROPRIO token (stringa vuota) non deve toccare i token
    registrati da altri utenti — la deduplica scatta solo su un token non
    vuoto che coincide davvero."""
    a = utenti_repo.create(email="a@esempio.it", password="password1234", ruolo="esterno")
    b = utenti_repo.create(email="b@esempio.it", password="password1234", ruolo="esterno")
    utenti_repo.set_fcm_token(a.id, "token-a")

    utenti_repo.set_fcm_token(b.id, "")

    assert utenti_repo.get(a.id).fcm_token == "token-a"
    assert utenti_repo.get(b.id).fcm_token == ""


# --------------------------------------------------------------------------- #
#  POST /portale/fcm-token
# --------------------------------------------------------------------------- #


def test_portale_fcm_token_richiede_login() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.post("/portale/fcm-token", data={"fcm_token": "abc"})
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_portale_fcm_token_salva_per_utente_loggato(authenticated_app) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/fcm-token",
        data={
            "fcm_token": "device-token-xyz",
            "csrf_token": get_csrf(client, "/portale/impostazioni"),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert authenticated_app.get(esterno.id).fcm_token == "device-token-xyz"
    # default platform "android" (retrocompatibilità con le build app già in
    # circolazione, che non mandano ancora il campo platform): non deve mai
    # toccare fcm_token_web.
    assert authenticated_app.get(esterno.id).fcm_token_web == ""


def test_portale_fcm_token_platform_web_salva_su_colonna_separata(authenticated_app) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/fcm-token",
        data={
            "fcm_token": "web-token-xyz",
            "platform": "web",
            "csrf_token": get_csrf(client, "/portale/impostazioni"),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    aggiornato = authenticated_app.get(esterno.id)
    assert aggiornato.fcm_token_web == "web-token-xyz"
    # non deve toccare il token app: un utente può avere entrambi i canali
    # attivi contemporaneamente (app Android + portale in browser).
    assert aggiornato.fcm_token == ""


def test_portale_fcm_token_app_e_web_coesistono(authenticated_app) -> None:
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")
    csrf = get_csrf(client, "/portale/impostazioni")

    client.post(
        "/portale/fcm-token",
        data={"fcm_token": "android-token", "platform": "android", "csrf_token": csrf},
    )
    client.post(
        "/portale/fcm-token",
        data={"fcm_token": "web-token", "platform": "web", "csrf_token": csrf},
    )
    aggiornato = authenticated_app.get(esterno.id)
    assert aggiornato.fcm_token == "android-token"
    assert aggiornato.fcm_token_web == "web-token"


def test_set_fcm_token_web_salva_e_sovrascrive(utenti_repo: UtentiRepository) -> None:
    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", ruolo="esterno"
    )
    assert esterno.fcm_token_web == ""

    utenti_repo.set_fcm_token_web(esterno.id, "web-token-1")
    assert utenti_repo.get(esterno.id).fcm_token_web == "web-token-1"

    utenti_repo.set_fcm_token_web(esterno.id, "web-token-2")
    assert utenti_repo.get(esterno.id).fcm_token_web == "web-token-2"


# --------------------------------------------------------------------------- #
#  Gating FCM in _notifica_esterni_assegnati / _notifica_esterno_assegnazione
# --------------------------------------------------------------------------- #


def test_admin_nota_manda_fcm_se_esterno_ha_token(admin_client, authenticated_app) -> None:
    # notify_push_enabled resta False (default): FCM è un canale indipendente
    # dalla preferenza ntfy, un token registrato dall'app è già opt-in.
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_fcm_token(esterno.id, "device-token-xyz")
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    with patch("lys_workflow_hub.web.routes.notify_fcm_nuova_attivita") as mock_fcm:
        resp = client.post(
            "/pratiche/766/note",
            data={"testo": "aggiornamento", "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_fcm.assert_called_once()
        assert mock_fcm.call_args.kwargs["fcm_token"] == "device-token-xyz"


def test_assegna_pratica_manda_fcm_se_esterno_ha_token(admin_client, authenticated_app) -> None:
    # notify_push_enabled resta False (default): stesso motivo di sopra.
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_fcm_token(esterno.id, "device-token-xyz")

    with patch("lys_workflow_hub.web.routes.notify_fcm_nuova_attivita") as mock_fcm:
        resp = client.post(
            "/pratiche/766/assegna",
            data={"utente_id": esterno.id, "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_fcm.assert_called_once()
        assert mock_fcm.call_args.kwargs["fcm_token"] == "device-token-xyz"


def test_admin_nota_manda_fcm_su_entrambi_i_canali_se_presenti(
    admin_client, authenticated_app
) -> None:
    # Un utente può avere sia l'app Android sia il portale in browser
    # registrati: entrambi i token (colonne indipendenti) devono ricevere,
    # non solo uno dei due.
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_fcm_token(esterno.id, "android-token")
    authenticated_app.set_fcm_token_web(esterno.id, "web-token")
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    with patch("lys_workflow_hub.web.routes.notify_fcm_nuova_attivita") as mock_fcm:
        resp = client.post(
            "/pratiche/766/note",
            data={"testo": "aggiornamento", "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        assert mock_fcm.call_count == 2
        token_inviati = {c.kwargs["fcm_token"] for c in mock_fcm.call_args_list}
        assert token_inviati == {"android-token", "web-token"}


def test_assegna_pratica_manda_fcm_su_entrambi_i_canali_se_presenti(
    admin_client, authenticated_app
) -> None:
    # Stesso caso multi-canale del test sopra, ma sull'altro call site
    # refactorizzato in _notifica_fcm_tutti_i_canali (assegnazione pratica
    # invece di nuova nota) — deve valere identico su entrambi.
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_fcm_token(esterno.id, "android-token")
    authenticated_app.set_fcm_token_web(esterno.id, "web-token")

    with patch("lys_workflow_hub.web.routes.notify_fcm_nuova_attivita") as mock_fcm:
        resp = client.post(
            "/pratiche/766/assegna",
            data={"utente_id": esterno.id, "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        assert mock_fcm.call_count == 2
        token_inviati = {c.kwargs["fcm_token"] for c in mock_fcm.call_args_list}
        assert token_inviati == {"android-token", "web-token"}


def test_admin_nota_manda_fcm_solo_su_web_se_solo_quello_presente(
    admin_client, authenticated_app
) -> None:
    client, settings = admin_client
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    authenticated_app.set_fcm_token_web(esterno.id, "web-token")
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    with patch("lys_workflow_hub.web.routes.notify_fcm_nuova_attivita") as mock_fcm:
        resp = client.post(
            "/pratiche/766/note",
            data={"testo": "aggiornamento", "csrf_token": get_csrf(client, "/pratiche/766")},
        )
        assert resp.status_code == 303
        mock_fcm.assert_called_once()
        assert mock_fcm.call_args.kwargs["fcm_token"] == "web-token"
