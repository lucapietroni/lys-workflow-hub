"""Pagina admin: ricerca veicolo per targa (API Automotive di Openapi).

Route esposte:
    GET /targa    Form di ricerca; se `targa` e' valorizzata mostra anche l'esito.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.integrations.automotive import build_automotive_client
from lys_workflow_hub.web.auth import require_admin, template_context_processor

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["targa"], dependencies=[Depends(require_admin)])


@router.get("/targa", response_class=HTMLResponse)
def targa_lookup(
    request: Request,
    targa: str | None = None,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    context = {"version": __version__, "targa": targa or "", "veicolo": None}

    if targa and targa.strip():
        client = build_automotive_client(settings)
        try:
            context["veicolo"] = client.cerca_veicolo(targa.strip())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ricerca targa %s fallita", targa)
            context["errore_generico"] = f"Ricerca non riuscita: {exc}"

    return templates.TemplateResponse(request, "targa_lookup.html", context)
