"""Login / logout.

Route esposte:
    GET  /login     Form di accesso (pubblico, vedi AuthMiddleware)
    POST /login      Verifica credenziali, apre sessione
    POST /logout     Chiude la sessione
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.core.utenti_repository import AuthError, Utente, UtentiRepository
from lys_workflow_hub.web.auth import new_csrf_token, verify_csrf


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["auth"])


def get_utenti_repo(request: Request) -> UtentiRepository:
    """Singleton condiviso con `AuthMiddleware` — vedi `app.state.utenti_repo` in main.py."""
    return request.app.state.utenti_repo


def _sanitize_next(next_path: str | None) -> str:
    """Evita open-redirect: accetta solo path locali (`/qualcosa`).

    Ritorna stringa vuota se assente/non valido — NON un fallback a "/",
    altrimenti il campo hidden "next" nel form finirebbe sempre valorizzato
    e il redirect di default per-ruolo (`_default_landing`) non scatterebbe
    mai (bug reale osservato: esterno sempre rimandato su "/" → 403)."""
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return ""


def _default_landing(utente: Utente) -> str:
    """Pagina di atterraggio quando non è stato richiesto un `next` esplicito.

    "/" è admin-only (routes.py monta require_admin a livello di router),
    quindi gli utenti esterni/supervisore vanno mandati su /portale.
    L'operatore ha un'unica pagina, /operatore (crea ingressi officina) —
    /portale gli mostrerebbe una lista pratiche vuota e fuori contesto.
    """
    if utente.is_admin:
        return "/"
    if utente.is_operatore:
        return "/operatore"
    return "/portale"


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "") -> HTMLResponse:
    current_user = getattr(request.state, "current_user", None)
    if current_user is not None:
        redirect_to = _sanitize_next(next) or _default_landing(current_user)
        return RedirectResponse(url=redirect_to, status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "version": __version__,
            "csrf_token": new_csrf_token(request),
            "next": _sanitize_next(next),
            "error": None,
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    repo: UtentiRepository = Depends(get_utenti_repo),
) -> HTMLResponse:
    form = await request.form()
    email = str(form.get("email") or "").strip()
    password = str(form.get("password") or "")
    csrf_token = str(form.get("csrf_token") or "")
    next_path = _sanitize_next(str(form.get("next") or ""))

    if not verify_csrf(request, csrf_token):
        error = "Sessione scaduta, riprova."
    else:
        try:
            utente = repo.authenticate(email, password)
        except AuthError as exc:
            error = str(exc)
        else:
            # Rigenera la sessione (nuovo cookie) per evitare session fixation.
            request.session.clear()
            request.session["user_id"] = utente.id
            logger.info("Login riuscito: %s (ruolo=%s)", utente.email, utente.ruolo)
            redirect_to = next_path or _default_landing(utente)
            return RedirectResponse(url=redirect_to, status_code=303)

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "version": __version__,
            "csrf_token": new_csrf_token(request),
            "next": next_path,
            "error": error,
        },
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
