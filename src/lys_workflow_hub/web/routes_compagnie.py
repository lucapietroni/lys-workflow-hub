"""Pagine HTML per la gestione dell'anagrafica delle compagnie assicurative.

Route esposte:
    GET  /compagnie                     Lista compagnie + link a creazione/modifica
    GET  /compagnie/nuova               Form di inserimento
    POST /compagnie/nuova               Creazione record
    GET  /compagnie/{id}                Form di modifica precompilato
    POST /compagnie/{id}                Aggiornamento record
    POST /compagnie/{id}/elimina        Cancellazione record
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.compagnie_repository import (
    Compagnia,
    CompagnieRepository,
)


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["compagnie"])


def get_compagnie_repo(
    settings: Settings = Depends(get_settings),
) -> CompagnieRepository:
    return CompagnieRepository(db_path=settings.app_db_path)


def _common_context() -> dict:
    return {"version": __version__}


def _form_to_dict(form) -> dict:
    """Normalizza una FormData in dict[str, str] con strip."""
    out: dict[str, str] = {}
    for key in form.keys():
        value = form.get(key)
        out[key] = value.strip() if isinstance(value, str) else (value or "")
    return out


def _parse_int_or_none(s: str) -> int | None:
    """Converte stringa in int oppure None se vuota/non numerica."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _render_form(
    request: Request,
    *,
    compagnia: Compagnia | None,
    error: str | None,
    form_values: dict | None,
    sla_defaults: dict | None = None,
) -> HTMLResponse:
    context = _common_context()
    context["compagnia"] = compagnia
    context["error"] = error
    # sla_defaults: valori globali da mostrare come placeholder nei campi SLA.
    context["sla_defaults"] = sla_defaults or {"sollecito": 15, "formale": 30, "diffida": 45}
    # Pre-fill: se è una correzione di un POST fallito, mostro i valori inviati;
    # altrimenti uso quelli del record (in modifica) o stringhe vuote (in nuovo).
    if form_values is not None:
        context["values"] = form_values
    elif compagnia is not None:
        context["values"] = {
            "nome": compagnia.nome,
            "pec": compagnia.pec,
            "email": compagnia.email,
            "telefono": compagnia.telefono,
            "indirizzo": compagnia.indirizzo,
            "cap": compagnia.cap,
            "citta": compagnia.citta,
            "provincia": compagnia.provincia,
            "ufficio_sinistri": compagnia.ufficio_sinistri,
            "note": compagnia.note,
            "sla_sollecito_giorni": (
                str(compagnia.sla_sollecito_giorni)
                if compagnia.sla_sollecito_giorni is not None else ""
            ),
            "sla_formale_giorni": (
                str(compagnia.sla_formale_giorni)
                if compagnia.sla_formale_giorni is not None else ""
            ),
            "sla_diffida_giorni": (
                str(compagnia.sla_diffida_giorni)
                if compagnia.sla_diffida_giorni is not None else ""
            ),
        }
    else:
        # Nuova compagnia: pre-popola con i default globali.
        d = context["sla_defaults"]
        context["values"] = {
            "nome": "", "pec": "", "email": "", "telefono": "", "indirizzo": "",
            "cap": "", "citta": "", "provincia": "", "ufficio_sinistri": "",
            "note": "",
            "sla_sollecito_giorni": str(d["sollecito"]),
            "sla_formale_giorni": str(d["formale"]),
            "sla_diffida_giorni": str(d["diffida"]),
        }
    return templates.TemplateResponse(request, "compagnia_form.html", context)


# --------------------------------------------------------------------------- #
#  Lista
# --------------------------------------------------------------------------- #


@router.get("/compagnie", response_class=HTMLResponse)
def compagnie_list(
    request: Request,
    repo: CompagnieRepository = Depends(get_compagnie_repo),
) -> HTMLResponse:
    context = _common_context()
    context["compagnie"] = repo.list_all()
    return templates.TemplateResponse(request, "compagnie_list.html", context)


# --------------------------------------------------------------------------- #
#  Crea
# --------------------------------------------------------------------------- #


@router.get("/compagnie/nuova", response_class=HTMLResponse)
def compagnia_new_form(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    sla_defaults = {
        "sollecito": settings.sla_giorni_alert,
        "formale": settings.sla_formale_giorni,
        "diffida": settings.sla_diffida_giorni,
    }
    return _render_form(
        request, compagnia=None, error=None, form_values=None,
        sla_defaults=sla_defaults,
    )


@router.post("/compagnie/nuova")
async def compagnia_new_submit(
    request: Request,
    repo: CompagnieRepository = Depends(get_compagnie_repo),
    settings: Settings = Depends(get_settings),
):
    form = await request.form()
    values = _form_to_dict(form)
    sla_defaults = {
        "sollecito": settings.sla_giorni_alert,
        "formale": settings.sla_formale_giorni,
        "diffida": settings.sla_diffida_giorni,
    }
    try:
        repo.create(
            nome=values.get("nome", ""),
            pec=values.get("pec", ""),
            email=values.get("email", ""),
            telefono=values.get("telefono", ""),
            indirizzo=values.get("indirizzo", ""),
            cap=values.get("cap", ""),
            citta=values.get("citta", ""),
            provincia=values.get("provincia", ""),
            ufficio_sinistri=values.get("ufficio_sinistri", ""),
            note=values.get("note", ""),
            sla_sollecito_giorni=_parse_int_or_none(values.get("sla_sollecito_giorni", "")),
            sla_formale_giorni=_parse_int_or_none(values.get("sla_formale_giorni", "")),
            sla_diffida_giorni=_parse_int_or_none(values.get("sla_diffida_giorni", "")),
        )
    except ValueError as exc:
        return _render_form(
            request, compagnia=None, error=str(exc), form_values=values,
            sla_defaults=sla_defaults,
        )
    return RedirectResponse(url="/compagnie", status_code=303)


# --------------------------------------------------------------------------- #
#  Modifica
# --------------------------------------------------------------------------- #


@router.get("/compagnie/{compagnia_id}", response_class=HTMLResponse)
def compagnia_edit_form(
    compagnia_id: int,
    request: Request,
    repo: CompagnieRepository = Depends(get_compagnie_repo),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    compagnia = repo.get(compagnia_id)
    if compagnia is None:
        raise HTTPException(404, f"Compagnia id={compagnia_id} non trovata.")
    sla_defaults = {
        "sollecito": settings.sla_giorni_alert,
        "formale": settings.sla_formale_giorni,
        "diffida": settings.sla_diffida_giorni,
    }
    return _render_form(
        request, compagnia=compagnia, error=None, form_values=None,
        sla_defaults=sla_defaults,
    )


@router.post("/compagnie/{compagnia_id}")
async def compagnia_edit_submit(
    compagnia_id: int,
    request: Request,
    repo: CompagnieRepository = Depends(get_compagnie_repo),
    settings: Settings = Depends(get_settings),
):
    existing = repo.get(compagnia_id)
    if existing is None:
        raise HTTPException(404, f"Compagnia id={compagnia_id} non trovata.")
    form = await request.form()
    values = _form_to_dict(form)
    sla_defaults = {
        "sollecito": settings.sla_giorni_alert,
        "formale": settings.sla_formale_giorni,
        "diffida": settings.sla_diffida_giorni,
    }
    try:
        repo.update(
            compagnia_id,
            nome=values.get("nome", ""),
            pec=values.get("pec", ""),
            email=values.get("email", ""),
            telefono=values.get("telefono", ""),
            indirizzo=values.get("indirizzo", ""),
            cap=values.get("cap", ""),
            citta=values.get("citta", ""),
            provincia=values.get("provincia", ""),
            ufficio_sinistri=values.get("ufficio_sinistri", ""),
            note=values.get("note", ""),
            sla_sollecito_giorni=_parse_int_or_none(values.get("sla_sollecito_giorni", "")),
            sla_formale_giorni=_parse_int_or_none(values.get("sla_formale_giorni", "")),
            sla_diffida_giorni=_parse_int_or_none(values.get("sla_diffida_giorni", "")),
        )
    except ValueError as exc:
        return _render_form(
            request, compagnia=existing, error=str(exc), form_values=values,
            sla_defaults=sla_defaults,
        )
    return RedirectResponse(url="/compagnie", status_code=303)


# --------------------------------------------------------------------------- #
#  Elimina
# --------------------------------------------------------------------------- #


@router.post("/compagnie/{compagnia_id}/elimina")
def compagnia_delete(
    compagnia_id: int,
    repo: CompagnieRepository = Depends(get_compagnie_repo),
) -> RedirectResponse:
    if not repo.delete(compagnia_id):
        raise HTTPException(404, f"Compagnia id={compagnia_id} non trovata.")
    return RedirectResponse(url="/compagnie", status_code=303)
