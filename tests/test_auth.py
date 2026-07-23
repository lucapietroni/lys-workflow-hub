"""Test dell'autenticazione (v3.0 fase 1): repository utenti + route login/logout."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.core.utenti_repository import AuthError, UtentiRepository
from lys_workflow_hub.main import app
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, get_csrf, login_as, login_as_admin


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


# --------------------------------------------------------------------------- #
#  UtentiRepository
# --------------------------------------------------------------------------- #


def test_create_and_authenticate(tmp_path: Path) -> None:
    repo = UtentiRepository(db_path=tmp_path / "u.db")
    repo.create(email="Test@Example.com", password="password123", ruolo="admin")

    utente = repo.authenticate("test@example.com", "password123")
    assert utente.email == "test@example.com"
    assert utente.is_admin
    assert utente.last_login is not None


def test_authenticate_wrong_password_raises(tmp_path: Path) -> None:
    repo = UtentiRepository(db_path=tmp_path / "u.db")
    repo.create(email="a@b.it", password="password123")

    with pytest.raises(AuthError):
        repo.authenticate("a@b.it", "sbagliata")


def test_authenticate_unknown_email_raises(tmp_path: Path) -> None:
    repo = UtentiRepository(db_path=tmp_path / "u.db")
    with pytest.raises(AuthError):
        repo.authenticate("nonexiste@nte.it", "qualcosa123")


def test_authenticate_locks_after_max_attempts(tmp_path: Path) -> None:
    repo = UtentiRepository(db_path=tmp_path / "u.db", max_attempts=3, lockout_minutes=15)
    repo.create(email="a@b.it", password="password123")

    for _ in range(3):
        with pytest.raises(AuthError):
            repo.authenticate("a@b.it", "sbagliata")

    # Quarto tentativo, anche con la password corretta: account bloccato.
    with pytest.raises(AuthError, match="Troppi tentativi"):
        repo.authenticate("a@b.it", "password123")


def test_authenticate_disabled_user_raises(tmp_path: Path) -> None:
    repo = UtentiRepository(db_path=tmp_path / "u.db")
    utente = repo.create(email="a@b.it", password="password123")
    repo.set_attivo(utente.id, False)

    with pytest.raises(AuthError, match="disattivato"):
        repo.authenticate("a@b.it", "password123")


def test_create_duplicate_email_raises(tmp_path: Path) -> None:
    repo = UtentiRepository(db_path=tmp_path / "u.db")
    repo.create(email="a@b.it", password="password123")
    with pytest.raises(ValueError, match="già registrata"):
        repo.create(email="a@b.it", password="altrapassword")


def test_create_short_password_raises(tmp_path: Path) -> None:
    repo = UtentiRepository(db_path=tmp_path / "u.db")
    with pytest.raises(ValueError, match="8 caratteri"):
        repo.create(email="a@b.it", password="corta")


# --------------------------------------------------------------------------- #
#  Route /login, /logout + protezione route
# --------------------------------------------------------------------------- #


def test_protected_route_redirects_to_login_when_anonymous(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_health_is_public(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    response = client.get("/health")
    assert response.status_code == 200


def test_login_wrong_password_shows_error(authenticated_app) -> None:
    client = TestClient(app)
    resp = client.get("/login")
    csrf = _CSRF_RE.search(resp.text).group(1)

    resp = client.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": "sbagliata", "csrf_token": csrf, "next": "/"},
    )
    assert resp.status_code == 401
    assert "non corretti" in resp.text


def test_login_bad_csrf_rejected(authenticated_app) -> None:
    client = TestClient(app)
    client.get("/login")  # inizializza la sessione

    resp = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "csrf_token": "token-falso",
            "next": "/",
        },
    )
    assert resp.status_code == 401
    assert "scaduta" in resp.text


def test_post_generico_senza_csrf_token_rifiutato(authenticated_app) -> None:
    """`AuthMiddleware` verifica il csrf_token su OGNI POST autenticato
    (tranne /login, che ha il proprio test dedicato sopra) — qui su /logout,
    scelto perché non richiede dati applicativi."""
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)

    resp = client.post("/logout")  # niente csrf_token nel body
    assert resp.status_code == 403
    assert "sicurezza" in resp.json()["detail"].lower()

    # La sessione deve restare valida: il logout NON deve essere avvenuto.
    resp = client.get("/")
    assert resp.status_code != 303


def test_post_generico_con_csrf_token_falso_rifiutato(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)

    resp = client.post("/logout", data={"csrf_token": "token-falso"})
    assert resp.status_code == 403


def test_login_success_then_logout(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)

    # Sessione valida: la home non reindirizza più a /login.
    resp = client.get("/")
    assert resp.status_code != 303

    resp = client.post("/logout", data={"csrf_token": get_csrf(client, "/")})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    # Sessione chiusa: torna a essere bloccata.
    resp = client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_login_esterno_senza_next_atterra_su_portale(authenticated_app) -> None:
    """"/" è admin-only: un esterno senza `next` esplicito deve finire su
    /portale, non su "/" (altrimenti prenderebbe un 403, vedi bug in prod)."""
    authenticated_app.create(
        email="esterno@test.local", password="password1234", nome="Esterno Test", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/login")
    csrf = _CSRF_RE.search(resp.text).group(1)

    resp = client.post(
        "/login",
        data={"email": "esterno@test.local", "password": "password1234", "csrf_token": csrf},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portale"


def test_esterno_con_sessione_valida_apre_root_redirige_a_portale(authenticated_app) -> None:
    """Bug reale segnalato in produzione: un esterno già loggato (sessione
    valida da prima) che apre semplicemente hub.lysauto.it (bookmark, home
    del browser) atterrava sul 403 JSON grezzo di require_admin invece di
    un redirect amichevole — "/" è admin-only, ma è anche l'URL "nudo" che
    chiunque digita. Qui il login avviene PRIMA, poi si naviga su "/" come
    richiesta indipendente (non redirect di login), per riprodurre esattamente
    lo scenario segnalato."""
    authenticated_app.create(
        email="esterno2@test.local", password="password1234", nome="Esterno Test", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "esterno2@test.local", "password1234")

    resp = client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portale"


def test_login_form_next_hidden_field_vuoto_senza_query_param(authenticated_app) -> None:
    """Il campo hidden "next" NON deve mai essere pre-valorizzato con "/":
    altrimenti il browser reale lo rispedisce sempre nel POST e il redirect
    per-ruolo (_default_landing) non scatta mai — bug reale già visto in prod."""
    resp = TestClient(app).get("/login")
    assert 'name="next" value=""' in resp.text


def test_login_esterno_flusso_browser_reale_atterra_su_portale(authenticated_app) -> None:
    """Replica esatta del flusso browser: legge il campo hidden "next" dalla
    pagina (invece di ometterlo) e lo rispedisce nel POST, come farebbe un
    form HTML reale."""
    authenticated_app.create(
        email="esterno3@test.local", password="password1234", nome="Esterno3", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/login")
    csrf = _CSRF_RE.search(resp.text).group(1)
    next_match = re.search(r'name="next" value="([^"]*)"', resp.text)
    assert next_match, "campo hidden next non trovato"

    resp = client.post(
        "/login",
        data={
            "email": "esterno3@test.local",
            "password": "password1234",
            "csrf_token": csrf,
            "next": next_match.group(1),
        },
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portale"


def test_login_form_esterno_gia_autenticato_redirige_a_portale(authenticated_app) -> None:
    authenticated_app.create(
        email="esterno2@test.local", password="password1234", nome="Esterno2", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "esterno2@test.local", "password1234")

    resp = client.get("/login")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portale"
