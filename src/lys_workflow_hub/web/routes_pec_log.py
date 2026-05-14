"""Pagine HTML per la cronologia (audit) delle PEC inviate (M2-bis).

Route esposte:
    GET  /pec-inviate                       Lista cronologica
    GET  /pec-inviate/{id}                  Dettaglio di un invio
    GET  /pec-inviate/{id}/scarica          Download del file .eml archiviato
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.pec_log_repository import PecLogRepository


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pec_log"])


def get_pec_log_repo(
    settings: Settings = Depends(get_settings),
) -> PecLogRepository:
    return PecLogRepository(db_path=settings.app_db_path)


@router.get("/pec-inviate", response_class=HTMLResponse)
def pec_list(
    request: Request,
    pec_log: PecLogRepository = Depends(get_pec_log_repo),
) -> HTMLResponse:
    records = pec_log.list_all(limit=200)
    return templates.TemplateResponse(
        request,
        "pec_inviate_list.html",
        {"version": __version__, "records": records},
    )


@router.get("/pec-inviate/{pec_id}", response_class=HTMLResponse)
def pec_detail(
    pec_id: int,
    request: Request,
    pec_log: PecLogRepository = Depends(get_pec_log_repo),
) -> HTMLResponse:
    record = pec_log.get(pec_id)
    if record is None:
        raise HTTPException(404, f"PEC id={pec_id} non trovata.")
    return templates.TemplateResponse(
        request,
        "pec_inviata_detail.html",
        {"version": __version__, "record": record},
    )


@router.get("/pec-inviate/{pec_id}/scarica")
def pec_download_eml(
    pec_id: int,
    pec_log: PecLogRepository = Depends(get_pec_log_repo),
) -> Response:
    record = pec_log.get(pec_id)
    if record is None:
        raise HTTPException(404, f"PEC id={pec_id} non trovata.")
    if not record.path_eml:
        raise HTTPException(404, "File .eml non disponibile per questo record.")
    eml_path = Path(record.path_eml)
    if not eml_path.exists():
        raise HTTPException(
            410,  # Gone: il record esiste ma il file no
            f"File .eml non più presente sul filesystem: {record.path_eml}",
        )
    return FileResponse(
        path=eml_path,
        filename=eml_path.name,
        media_type="message/rfc822",
    )
