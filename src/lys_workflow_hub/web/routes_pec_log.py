"""Pagine HTML per la cronologia (audit) delle PEC inviate (M2-bis).

Route esposte:
    GET  /pec-inviate                       Lista cronologica
    GET  /pec-inviate/{id}                  Dettaglio di un invio
    GET  /pec-inviate/{id}/scarica          Download del file .eml archiviato
    GET  /pec-inviate/{id}/invia-email      Anteprima email (corpo editabile + allegati)
    POST /pec-inviate/{id}/invia-email      Esegue invio via email ordinaria
"""
from __future__ import annotations

import email as _email_lib
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.compagnie_repository import CompagnieRepository
from lys_workflow_hub.core.pec_log_repository import PecLogRepository
from lys_workflow_hub.workflows.risarcimento_vandalismo.invio_pec import (
    invia_email_ordinaria,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.data import (
    CARROZZERIA_NOME as VAND_CARROZZERIA_NOME,
)


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pec_log"])


def get_pec_log_repo(
    settings: Settings = Depends(get_settings),
) -> PecLogRepository:
    return PecLogRepository(db_path=settings.app_db_path)


def get_compagnie_repo(
    settings: Settings = Depends(get_settings),
) -> CompagnieRepository:
    return CompagnieRepository(db_path=settings.app_db_path)


def _estrai_body_da_eml(eml_path: Path) -> str:
    """Estrae il corpo testuale (text/plain) da un file .eml."""
    raw = eml_path.read_bytes()
    msg = _email_lib.message_from_bytes(raw)
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            charset = part.get_content_charset() or "utf-8"
            try:
                return (part.get_payload(decode=True) or b"").decode(charset, errors="replace")
            except Exception:
                pass
    return ""


def _trova_allegati_pratica(
    numero_pratica: int, nomi: list[str], archivio_root: Path
) -> list[Path]:
    """Cerca i file allegati per nome nella cartella della pratica WinCar."""
    cartella = archivio_root / "Pratiche" / str(numero_pratica)
    if not cartella.exists():
        return []
    trovati: list[Path] = []
    for nome in nomi:
        matches = list(cartella.rglob(nome))
        if matches:
            trovati.append(matches[0])
    return trovati


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
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
) -> HTMLResponse:
    record = pec_log.get(pec_id)
    if record is None:
        raise HTTPException(404, f"PEC id={pec_id} non trovata.")
    compagnia = None
    if record.compagnia_id:
        compagnia = compagnie_repo.get(record.compagnia_id)
    return templates.TemplateResponse(
        request,
        "pec_inviata_detail.html",
        {
            "version": __version__,
            "record": record,
            "compagnia": compagnia,
            "email_inviata": request.query_params.get("email_inviata") == "1",
        },
    )


def _load_email_context(
    pec_id: int,
    pec_log: PecLogRepository,
    compagnie_repo: CompagnieRepository,
    settings: Settings,
):
    """Carica record + compagnia + body + allegati_info. Usato da GET e POST."""
    record = pec_log.get(pec_id)
    if record is None:
        raise HTTPException(404, f"PEC id={pec_id} non trovata.")
    if not record.compagnia_id:
        raise HTTPException(400, "PEC senza compagnia associata: impossibile trovare l'email.")
    compagnia = compagnie_repo.get(record.compagnia_id)
    if not compagnia or not compagnia.email:
        raise HTTPException(400, "Compagnia senza email ordinaria configurata.")

    body = ""
    if record.path_eml:
        eml_path = Path(record.path_eml)
        if eml_path.exists():
            body = _estrai_body_da_eml(eml_path)
    if not body:
        body = record.body_excerpt

    # Lista allegati con flag trovato/non trovato.
    allegati_info: list[dict] = []
    if record.allegati:
        cartella = settings.wincar_archivio / "Pratiche" / str(record.numero_pratica)
        for nome in record.allegati:
            trovato = cartella.exists() and bool(list(cartella.rglob(nome)))
            allegati_info.append({"nome": nome, "trovato": trovato})

    return record, compagnia, body, allegati_info


@router.get("/pec-inviate/{pec_id}/invia-email", response_class=HTMLResponse)
def pec_invia_email_form(
    pec_id: int,
    request: Request,
    pec_log: PecLogRepository = Depends(get_pec_log_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Pagina di anteprima: corpo editabile + selezione allegati."""
    record, compagnia, body, allegati_info = _load_email_context(
        pec_id, pec_log, compagnie_repo, settings
    )
    return templates.TemplateResponse(
        request,
        "pec_email_conferma.html",
        {
            "version": __version__,
            "record": record,
            "compagnia": compagnia,
            "body": body,
            "allegati_info": allegati_info,
            "dry_run": bool(settings.pec_dry_run),
        },
    )


@router.post("/pec-inviate/{pec_id}/invia-email")
async def pec_invia_email(
    pec_id: int,
    request: Request,
    pec_log: PecLogRepository = Depends(get_pec_log_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Esegue l'invio via SMTP normale con il corpo e gli allegati scelti dall'operatore."""
    record, compagnia, _body_default, _allegati_info = _load_email_context(
        pec_id, pec_log, compagnie_repo, settings
    )

    form = await request.form()
    body = (form.get("body") or "").strip() or _body_default
    try:
        allegati_selezionati = form.getlist("allegati_selezionati")
    except AttributeError:
        allegati_selezionati = []

    allegati_paths = _trova_allegati_pratica(
        record.numero_pratica, allegati_selezionati, settings.wincar_archivio
    )

    sender_email = settings.smtp_from or settings.smtp_user
    sender_display = settings.carrozzeria_pec_alias or VAND_CARROZZERIA_NOME

    invia_email_ordinaria(
        pec_id=record.id,
        email_destinatario=compagnia.email,
        subject=record.oggetto,
        body=body,
        allegati_paths=allegati_paths,
        sender_email=sender_email,
        sender_display=sender_display,
        smtp_host=settings.smtp_host,
        smtp_port=int(settings.smtp_port),
        smtp_user=settings.smtp_user,
        smtp_password=settings.smtp_password,
        dry_run=bool(settings.pec_dry_run),
        repo=pec_log,
    )
    return RedirectResponse(url=f"/pec-inviate/{pec_id}?email_inviata=1", status_code=303)


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
