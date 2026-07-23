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
