"""Pagine HTML per le risposte assicurative classificate (M3).

Route esposte:
    GET  /risposte                          Lista cronologica + filtri
    GET  /risposte/{mail_id}                Dettaglio risposta + classificazione AI
    GET  /risposte/{mail_id}/scarica        Download del .eml grezzo
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.mail_in_repository import (
    CATEGORIA_LABELS,
    CATEGORIE,
    MailRepository,
)
from lys_workflow_hub.core.pec_log_repository import PecLogRepository


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["risposte"])


def get_mail_repo(
    settings: Settings = Depends(get_settings),
) -> MailRepository:
    return MailRepository(db_path=settings.app_db_path)


def get_pec_log_repo(
    settings: Settings = Depends(get_settings),
) -> PecLogRepository:
    return PecLogRepository(db_path=settings.app_db_path)


# --------------------------------------------------------------------------- #
#  Lista
# --------------------------------------------------------------------------- #


@router.get("/risposte", response_class=HTMLResponse)
def risposte_list(
    request: Request,
    categoria: str | None = None,
    only_action: bool = False,
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> HTMLResponse:
    records = mail_repo.list_con_classificazione(limit=300)
    if categoria and categoria in CATEGORIE:
        records = [r for r in records if r.categoria == categoria]
    if only_action:
        records = [r for r in records if r.action_required]
    return templates.TemplateResponse(
        request,
        "risposte_list.html",
        {
            "version": __version__,
            "records": records,
            "categoria": categoria,
            "only_action": only_action,
            "categorie": CATEGORIE,
            "categorie_labels": CATEGORIA_LABELS,
            "totale": len(records),
        },
    )


@router.get("/risposte/{mail_id}", response_class=HTMLResponse)
def risposta_detail(
    mail_id: int,
    request: Request,
    mail_repo: MailRepository = Depends(get_mail_repo),
    pec_repo: PecLogRepository = Depends(get_pec_log_repo),
) -> HTMLResponse:
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")
    classif = mail_repo.get_classification_for_mail(mail_id)
    pec_inviata = None
    if classif and classif.pec_inviata_id is not None:
        pec_inviata = pec_repo.get(classif.pec_inviata_id)
    return templates.TemplateResponse(
        request,
        "risposta_detail.html",
        {
            "version": __version__,
            "mail": mail,
            "classificazione": classif,
            "pec_inviata": pec_inviata,
        },
    )


@router.get("/risposte/{mail_id}/scarica")
def risposta_download_eml(
    mail_id: int,
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> Response:
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")
    if not mail.raw_eml_path:
        raise HTTPException(404, "File .eml non disponibile per questo record.")
    eml_path = Path(mail.raw_eml_path)
    if not eml_path.exists():
        raise HTTPException(
            410,
            f"File .eml non più presente sul filesystem: {mail.raw_eml_path}",
        )
    return FileResponse(
        path=eml_path,
        filename=eml_path.name,
        media_type="message/rfc822",
    )
