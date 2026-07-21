"""Autenticazione: sessione cookie, dependency di protezione route, CSRF.

Approccio:
  - `AuthMiddleware` gira su OGNI richiesta, carica l'utente dalla sessione
    (se presente) in `request.state.current_user`, e blocca (redirect a
    `/login`) qualunque percorso non nella allowlist se non c'è un utente
    loggato. Fail-closed: una route nuova aggiunta domani è protetta di
    default, non serve ricordarsi di aggiungere una dependency.
  - `require_admin` è una dependency FastAPI da aggiungere ai router che
    devono restare riservati agli operatori carrozzeria (tutti, per ora:
    il portale per utenti "esterno" arriva nelle fasi successive).
  - CSRF: token random legato alla sessione, verificato sui form POST
    sensibili (per ora solo login, che è raggiungibile anche senza sessione
    autenticata). L'estensione a tutti i form esistenti è lavoro futuro
    esplicitamente rimandato (v3.0 fase 2 - hardening).
"""
from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from lys_workflow_hub.core.utenti_repository import Utente


logger = logging.getLogger(__name__)

CSRF_SESSION_KEY = "csrf_token"

# Percorsi raggiungibili senza sessione autenticata.
PUBLIC_PATHS = {"/login", "/health"}
PUBLIC_PREFIXES = ("/static/",)


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Carica l'utente corrente dalla sessione; redirect a /login se assente.

    Legge il repository da `request.app.state.utenti_repo` (singleton creato
    in `main.py`, stesso pattern di `app.state.foto_repo`) invece di
    costruirselo da sé: cosi' i test possono sostituirlo con un DB temporaneo
    senza dover toccare `.env` / la cache di `get_settings()`.
    """

    async def dispatch(self, request: Request, call_next):
        repo = request.app.state.utenti_repo

        user: Utente | None = None
        user_id = request.session.get("user_id")
        if user_id is not None:
            try:
                candidate = repo.get(int(user_id))
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None and candidate.attivo:
                user = candidate
            else:
                # Utente cancellato/disattivato dopo il login: sessione morta.
                request.session.clear()

        request.state.current_user = user

        path = request.url.path
        if user is None and not _is_public(path):
            next_qs = f"?next={path}" if path != "/" else ""
            return RedirectResponse(url=f"/login{next_qs}", status_code=303)

        return await call_next(request)


def get_current_user(request: Request) -> Utente | None:
    """Dependency: utente loggato (o None). Popolato da `AuthMiddleware`."""
    return getattr(request.state, "current_user", None)


def require_admin(request: Request) -> Utente:
    """Dependency: 403 se l'utente corrente non è admin.

    `AuthMiddleware` garantisce già che ci sia un utente loggato (altrimenti
    la richiesta non arriva qui: è stata reindirizzata a /login prima).
    """
    user = getattr(request.state, "current_user", None)
    if user is None or not user.is_admin:
        raise HTTPException(status_code=403, detail="Accesso riservato agli amministratori.")
    return user


def template_context_processor(request: Request) -> dict:
    """Iniettato in ogni `Jinja2Templates(context_processors=[...])`.

    Rende `current_user` disponibile in tutti i template senza dover
    modificare ogni singola vista.
    """
    return {"current_user": getattr(request.state, "current_user", None)}


def new_csrf_token(request: Request) -> str:
    """Genera (o riusa) il token CSRF per la sessione corrente."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def verify_csrf(request: Request, submitted_token: str) -> bool:
    expected = request.session.get(CSRF_SESSION_KEY)
    return bool(expected) and secrets.compare_digest(expected, submitted_token or "")
