"""Web Push (portale in browser, v4.10.0): service worker alla root del
sito + script client-side in `base.html`, gated a utenti esterni con config
Firebase Web presente. Canale indipendente da FCM app (colonna
`fcm_token_web` separata da `fcm_token`, vedi `test_notifiche_preferenze.py`
per i test sul fan-out server-side).

Nota: `get_settings()` (usato sia da `main.firebase_messaging_sw` sia da
`auth.template_context_processor`) è cachato con `lru_cache` e chiamato
direttamente (non via `Depends`), quindi non è overridabile per-test come
`get_app_settings` — questi test verificano il comportamento con la config
reale presente in `.env` di sviluppo, non uno stato "non configurato"
sintetico (stessa limitazione pre-esistente di questo codebase per i test
FCM app, vedi `admin_client` in `test_notifiche_preferenze.py`).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from lys_workflow_hub.config import get_settings
from lys_workflow_hub.main import app
from tests.conftest import login_as, login_as_admin


def test_firebase_messaging_sw_pubblico_senza_login() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/firebase-messaging-sw.js")
    assert resp.status_code == 200


def test_firebase_messaging_sw_contenuto_valido() -> None:
    settings = get_settings()
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/firebase-messaging-sw.js")

    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert resp.headers["cache-control"] == "no-cache"
    assert "firebase.initializeApp(" in resp.text
    assert "onBackgroundMessage" in resp.text
    assert "notificationclick" in resp.text
    if settings.fcm_web_project_id:
        assert settings.fcm_web_project_id in resp.text


def test_portale_impostazioni_esterno_vede_script_web_push(authenticated_app) -> None:
    settings = get_settings()
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/impostazioni")
    assert resp.status_code == 200
    if settings.fcm_web_api_key and settings.fcm_web_vapid_key:
        assert "lysAttivaNotificheWeb" in resp.text
        assert "Attiva notifiche browser" in resp.text
        assert "firebase-messaging-sw.js" in resp.text


def test_admin_non_vede_script_web_push(authenticated_app) -> None:
    # Il canale Web Push è scoped agli utenti esterni (stesso ambito di FCM
    # app), gating via `current_user.ruolo == "esterno"` in base.html — un
    # admin non deve mai vedere lo script, indipendente da come è
    # configurato Firebase (a differenza dei test sopra, questo non dipende
    # dal contenuto di .env).
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "lysAttivaNotificheWeb" not in resp.text
