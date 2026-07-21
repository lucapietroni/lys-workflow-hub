"""Portale utenti esterni (v3.0 fase 3) — sola lettura, pratiche assegnate.

Nessuna `dependencies=[Depends(require_admin)]` qui: questo router è
l'eccezione "non admin-only" del progetto (vedi `web/auth.py`). Protetto
comunque da `AuthMiddleware` come tutto il resto (serve essere loggati),
ma qualunque utente (admin o esterno) può aprirlo — semplicemente vede solo
le pratiche assegnate al proprio account, che per un admin sono normalmente
zero. Le pagine di collaborazione (note, calendario) arrivano in fase 4:
per ora è solo un elenco di sola lettura.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.utenti_repository import Utente
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.web.auth import get_current_user, template_context_processor


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["portale"])


def get_assegnazioni_repo(
    settings: Settings = Depends(get_settings),
) -> PraticaAssegnazioniRepository:
    return PraticaAssegnazioniRepository(db_path=settings.app_db_path)


def get_wincar_repo(settings: Settings = Depends(get_settings)) -> WinCarRepository:
    return WinCarRepository.from_settings(settings)


@router.get("/portale", response_class=HTMLResponse)
def portale_list(
    request: Request,
    current_user: Utente | None = Depends(get_current_user),
    assegnazioni_repo: PraticaAssegnazioniRepository = Depends(get_assegnazioni_repo),
    wincar_repo: WinCarRepository = Depends(get_wincar_repo),
) -> HTMLResponse:
    numeri = (
        assegnazioni_repo.list_pratica_numeri_per_utente(current_user.id)
        if current_user
        else []
    )
    pratiche = []
    for numero in numeri:
        try:
            pratica = wincar_repo.get_pratica(numero)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Portale: impossibile leggere pratica %s da WinCar: %s", numero, exc)
            pratica = None
        if pratica is not None:
            pratiche.append(pratica)

    context = {"version": __version__, "pratiche": pratiche}
    return templates.TemplateResponse(request, "portale_list.html", context)
