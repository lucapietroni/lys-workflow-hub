"""Test delle route /utenti (CRUD admin, v3.0 fase 3)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from lys_workflow_hub.main import app
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, get_csrf, login_as, login_as_admin


def test_utenti_list_richiede_login(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/utenti")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_crea_utente_esterno(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)

    resp = client.get("/utenti/nuovo")
    assert resp.status_code == 200

    resp = client.post(
        "/utenti/nuovo",
        data={
            "email": "agenzia@esempio.it",
            "nome": "Agenzia Pratiche SRL",
            "ruolo": "esterno",
            "password": "password1234",
            "csrf_token": get_csrf(client, "/utenti/nuovo"),
        },
    )
    assert resp.status_code == 303

    resp = client.get("/utenti")
    assert resp.status_code == 200
    assert "agenzia@esempio.it" in resp.text
    assert "Agenzia Pratiche SRL" in resp.text


def test_utente_esterno_creato_puo_fare_login(authenticated_app) -> None:
    client_admin = TestClient(app)
    login_as_admin(client_admin)
    client_admin.post(
        "/utenti/nuovo",
        data={
            "email": "esterno@esempio.it",
            "nome": "Esterno Test",
            "ruolo": "esterno",
            "password": "password1234",
            "csrf_token": get_csrf(client_admin, "/utenti/nuovo"),
        },
    )

    client_esterno = TestClient(app, follow_redirects=False)
    login_as(client_esterno, "esterno@esempio.it", "password1234")

    # Un esterno non può vedere le pagine admin-only.
    resp = client_esterno.get("/utenti")
    assert resp.status_code == 403

    # Ma può aprire il portale.
    resp = client_esterno.get("/portale")
    assert resp.status_code == 200


def test_non_si_puo_disattivare_ultimo_admin(authenticated_app) -> None:
    client = TestClient(app)
    login_as_admin(client)

    admin = authenticated_app.get_by_email(ADMIN_EMAIL)
    resp = client.post(
        f"/utenti/{admin.id}",
        data={
            "nome": "Admin Test",
            "ruolo": "admin",  # niente "attivo" = checkbox non spuntata
            "csrf_token": get_csrf(client, "/utenti"),
        },
    )
    assert resp.status_code == 400
    assert "ultimo amministratore" in resp.text

    # L'admin è ancora attivo: verificalo rileggendo dal repository.
    assert authenticated_app.get(admin.id).attivo is True


def test_non_si_puo_eliminare_ultimo_admin(authenticated_app) -> None:
    client = TestClient(app)
    login_as_admin(client)

    admin = authenticated_app.get_by_email(ADMIN_EMAIL)
    resp = client.post(
        f"/utenti/{admin.id}/elimina", data={"csrf_token": get_csrf(client, "/utenti")}
    )
    assert resp.status_code == 400
    assert authenticated_app.get(admin.id) is not None


def test_secondo_admin_permette_di_disattivare_il_primo(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)
    client.post(
        "/utenti/nuovo",
        data={
            "email": "admin2@esempio.it",
            "nome": "Admin Due",
            "ruolo": "admin",
            "password": "password1234",
            "csrf_token": get_csrf(client, "/utenti/nuovo"),
        },
    )

    admin = authenticated_app.get_by_email(ADMIN_EMAIL)
    resp = client.post(
        f"/utenti/{admin.id}",
        data={
            "nome": "Admin Test",
            "ruolo": "admin",  # checkbox "attivo" assente
            "csrf_token": get_csrf(client, "/utenti"),
        },
    )
    assert resp.status_code == 303
    assert authenticated_app.get(admin.id).attivo is False
