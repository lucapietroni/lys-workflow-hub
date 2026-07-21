"""Gestione utenti applicativi (v3.0 fase 3) — CRUD riservato agli admin.

Route esposte:
    GET  /utenti                    Lista utenti
    GET  /utenti/nuovo              Form di creazione
    POST /utenti/nuovo              Crea utente
    GET  /utenti/{id}               Form di modifica
    POST /utenti/{id}               Aggiorna (nome, ruolo, attivo, password opzionale)
    POST /utenti/{id}/elimina       Elimina (hard delete)

Nessuna self-registration: solo un admin già loggato può creare altri
utenti. Il primo admin va creato via `scripts/create_admin.py`.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.core.utenti_repository import RUOLI, Utente, UtentiRepository
from lys_workflow_hub.web.auth import require_admin, template_context_processor


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["utenti"], dependencies=[Depends(require_admin)])


def get_utenti_repo(request: Request) -> UtentiRepository:
    """Singleton condiviso con `AuthMiddleware`/`routes_auth.py` — vedi
    `app.state.utenti_repo` in main.py. Stesso repository per tutta l'app,
    non una connessione nuova per router."""
    return request.app.state.utenti_repo


def _common_context() -> dict:
    return {"version": __version__}


@router.get("/utenti", response_class=HTMLResponse)
def utenti_list(
    request: Request, repo: UtentiRepository = Depends(get_utenti_repo)
) -> HTMLResponse:
    context = _common_context()
    context["utenti"] = repo.list_all()
    return templates.TemplateResponse(request, "utenti_list.html", context)


@router.get("/utenti/nuovo", response_class=HTMLResponse)
def utente_new_form(request: Request) -> HTMLResponse:
    context = _common_context()
    context["utente"] = None
    context["error"] = None
    context["values"] = {"email": "", "nome": "", "ruolo": "esterno"}
    context["ruoli"] = RUOLI
    return templates.TemplateResponse(request, "utente_form.html", context)


@router.post("/utenti/nuovo")
async def utente_new_submit(
    request: Request, repo: UtentiRepository = Depends(get_utenti_repo)
):
    form = await request.form()
    email = str(form.get("email") or "").strip()
    nome = str(form.get("nome") or "").strip()
    ruolo = str(form.get("ruolo") or "esterno").strip()
    password = str(form.get("password") or "")

    try:
        repo.create(email=email, password=password, nome=nome, ruolo=ruolo)  # type: ignore[arg-type]
    except ValueError as exc:
        context = _common_context()
        context["utente"] = None
        context["error"] = str(exc)
        context["values"] = {"email": email, "nome": nome, "ruolo": ruolo}
        context["ruoli"] = RUOLI
        return templates.TemplateResponse(
            request, "utente_form.html", context, status_code=400
        )
    return RedirectResponse(url="/utenti", status_code=303)


@router.get("/utenti/{utente_id}", response_class=HTMLResponse)
def utente_edit_form(
    utente_id: int, request: Request, repo: UtentiRepository = Depends(get_utenti_repo)
) -> HTMLResponse:
    utente = repo.get(utente_id)
    if utente is None:
        raise HTTPException(404, f"Utente id={utente_id} non trovato.")
    context = _common_context()
    context["utente"] = utente
    context["error"] = None
    context["values"] = {"email": utente.email, "nome": utente.nome, "ruolo": utente.ruolo}
    context["ruoli"] = RUOLI
    return templates.TemplateResponse(request, "utente_form.html", context)


@router.post("/utenti/{utente_id}")
async def utente_edit_submit(
    utente_id: int,
    request: Request,
    repo: UtentiRepository = Depends(get_utenti_repo),
    admin: Utente = Depends(require_admin),
):
    esistente = repo.get(utente_id)
    if esistente is None:
        raise HTTPException(404, f"Utente id={utente_id} non trovato.")

    form = await request.form()
    nome = str(form.get("nome") or "").strip()
    ruolo = str(form.get("ruolo") or esistente.ruolo).strip()
    attivo = form.get("attivo") == "on"
    nuova_password = str(form.get("password") or "")

    # Guard: non lasciare l'app senza nessun admin attivo.
    diventa_non_admin_o_disattivo = (ruolo != "admin") or not attivo
    if (
        esistente.is_admin
        and esistente.attivo
        and diventa_non_admin_o_disattivo
        and repo.count_admin_attivi() <= 1
    ):
        context = _common_context()
        context["utente"] = esistente
        context["error"] = (
            "Non puoi disattivare o retrocedere l'ultimo amministratore attivo: "
            "nessuno potrebbe più accedere per rimediare. Crea prima un altro admin."
        )
        context["values"] = {"email": esistente.email, "nome": nome, "ruolo": ruolo}
        context["ruoli"] = RUOLI
        return templates.TemplateResponse(
            request, "utente_form.html", context, status_code=400
        )

    try:
        if nuova_password:
            repo.set_password(utente_id, nuova_password)
        if ruolo != esistente.ruolo:
            repo.set_ruolo(utente_id, ruolo)  # type: ignore[arg-type]
        if nome != esistente.nome:
            repo.set_nome(utente_id, nome)
        if attivo != esistente.attivo:
            repo.set_attivo(utente_id, attivo)
    except ValueError as exc:
        context = _common_context()
        context["utente"] = esistente
        context["error"] = str(exc)
        context["values"] = {"email": esistente.email, "nome": nome, "ruolo": ruolo}
        context["ruoli"] = RUOLI
        return templates.TemplateResponse(
            request, "utente_form.html", context, status_code=400
        )
    return RedirectResponse(url="/utenti", status_code=303)


@router.post("/utenti/{utente_id}/elimina")
def utente_delete(
    utente_id: int, repo: UtentiRepository = Depends(get_utenti_repo)
) -> RedirectResponse:
    esistente = repo.get(utente_id)
    if esistente is None:
        raise HTTPException(404, f"Utente id={utente_id} non trovato.")
    if esistente.is_admin and esistente.attivo and repo.count_admin_attivi() <= 1:
        raise HTTPException(
            400,
            "Non puoi eliminare l'ultimo amministratore attivo: "
            "nessuno potrebbe più accedere per rimediare.",
        )
    repo.delete(utente_id)
    return RedirectResponse(url="/utenti", status_code=303)
