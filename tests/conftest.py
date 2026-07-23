"""Fixture condivise. Vedi in particolare `authenticated_app` (v3.0): da quando
`AuthMiddleware` protegge tutte le route non pubbliche, ogni test che usa
`TestClient(app)` per una pagina diversa da `/login` o `/health` deve prima
autenticarsi, altrimenti riceve un redirect 303 a `/login` invece della
risposta attesa.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.core.utenti_repository import UtentiRepository
from lys_workflow_hub.main import app


ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "test-password-1234"

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
_META_CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')


def get_csrf(client: TestClient, url: str) -> str:
    """Estrae il csrf_token corrente della sessione leggendo una pagina
    qualunque già raggiungibile dal client (iniettato in ogni pagina via
    `template_context_processor` + meta tag in `base.html`). Da usare per
    popolare `data={"csrf_token": ...}` nelle POST dei test, dato che
    `AuthMiddleware` ora verifica il token su ogni richiesta POST tranne
    `/login` (che ha il proprio flusso, vedi `login_as`)."""
    resp = client.get(url)
    match = _META_CSRF_RE.search(resp.text)
    assert match, f"csrf-token non trovato in GET {url} (status {resp.status_code})"
    return match.group(1)


def login_as(client: TestClient, email: str, password: str) -> None:
    """Esegue il flusso di login completo (GET csrf + POST credenziali) per
    un utente qualunque (admin o esterno)."""
    resp = client.get("/login")
    assert resp.status_code == 200, resp.text
    match = _CSRF_RE.search(resp.text)
    assert match, "csrf_token non trovato nella pagina di login"
    resp = client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": match.group(1),
            "next": "/",
        },
        follow_redirects=False,  # indipendente dal default del client chiamante
    )
    assert resp.status_code == 303, f"login fallito: {resp.status_code} {resp.text}"


def login_as_admin(client: TestClient) -> None:
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture
def utenti_repo(tmp_path: Path) -> UtentiRepository:
    """Repository utenti isolato su DB temporaneo, con un admin già creato."""
    repo = UtentiRepository(db_path=tmp_path / "utenti_test.db")
    repo.create(email=ADMIN_EMAIL, password=ADMIN_PASSWORD, nome="Admin Test", ruolo="admin")
    return repo


@pytest.fixture
def authenticated_app(utenti_repo: UtentiRepository) -> Iterator[UtentiRepository]:
    """Sostituisce `app.state.utenti_repo` con un repository di test.

    Sia `AuthMiddleware` che la route `/login` (`get_utenti_repo`) leggono da
    questo singleton, quindi basta un solo swap per allineare login e verifica
    sessione allo stesso DB temporaneo.
    """
    previous = getattr(app.state, "utenti_repo", None)
    app.state.utenti_repo = utenti_repo
    try:
        yield utenti_repo
    finally:
        if previous is not None:
            app.state.utenti_repo = previous
