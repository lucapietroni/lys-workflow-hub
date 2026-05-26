"""Pagine HTML per le risposte assicurative classificate (M3).

Route esposte:
    GET  /risposte                          Lista cronologica + filtri
    GET  /risposte/{mail_id}                Dettaglio risposta + classificazione AI
    GET  /risposte/{mail_id}/scarica        Download del .eml grezzo
    POST /risposte/{mail_id}/riclassifica   Re-estrae body+PDF e riclassifica con AI
    POST /risposte/{mail_id}/elimina        Elimina mail_in + classificazione dal DB
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.mail_in_repository import (
    CAT_ALTRO,
    CATEGORIA_LABELS,
    CATEGORIE,
    MailRepository,
)
from lys_workflow_hub.core.pec_log_repository import PecLogRepository
from lys_workflow_hub.integrations.ai_classifier import classify
from lys_workflow_hub.integrations.imap_fetcher import reextract_body
from lys_workflow_hub.workflows.risposte.matcher import match_mail


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
    show_all: bool = False,
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> HTMLResponse:
    """Lista risposte.

    Di default mostra solo le mail collegate a una PEC inviata
    (`solo_matched=True`): è il caso d'uso operativo, evita di vedere
    newsletter e ricevute di sistema. Con `?show_all=1` la query restituisce
    invece TUTTE le mail in archivio (utile per debug).
    """
    records = mail_repo.list_con_classificazione(
        limit=300, solo_matched=(not show_all),
    )
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
            "show_all": show_all,
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


# --------------------------------------------------------------------------- #
#  Riclassificazione manuale
# --------------------------------------------------------------------------- #


@router.post("/risposte/{mail_id}/riclassifica")
def risposta_riclassifica(
    mail_id: int,
    mail_repo: MailRepository = Depends(get_mail_repo),
    pec_repo: PecLogRepository = Depends(get_pec_log_repo),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Re-estrae body_text dal .eml (con fix PDF M5.3) e riclassifica con AI.

    Passi:
      1. Rilegge il file .eml dal filesystem.
      2. Aggiorna body_text in mail_in con l'estrazione corretta.
      3. Cancella la classificazione esistente.
      4. Esegue match + classify e salva la nuova classificazione.
    """
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")

    # 1) Re-estrai body_text dall'eml (applica fix M5.3: inner_msg + allegato hint).
    if mail.raw_eml_path:
        eml_path = Path(mail.raw_eml_path)
        if eml_path.exists():
            try:
                raw = eml_path.read_bytes()
                new_body, _has_att = reextract_body(
                    raw,
                    pdf_extract_enabled=bool(settings.pdf_extract_enabled),
                    pdf_extract_min_body_len=int(settings.pdf_extract_min_body_len),
                )
                mail_repo.update_body_text(mail_id, new_body)
                logger.info(
                    "Riclassifica mail %s: body aggiornato (%d→%d char)",
                    mail_id, len(mail.body_text), len(new_body),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Riclassifica mail %s: errore re-estrazione body: %s", mail_id, exc)
        else:
            logger.warning(
                "Riclassifica mail %s: .eml non trovato in %s, uso body esistente",
                mail_id, mail.raw_eml_path,
            )

    # 2) Cancella classificazione esistente.
    mail_repo.delete_classification_for_mail(mail_id)

    # 3) Ricarica mail con eventuale body aggiornato.
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(500, "Mail scomparsa dal DB dopo update.")

    # 4) Match + classify.
    match = match_mail(mail, pec_repo)
    if match.pratica_numero is None:
        mail_repo.save_classification(
            mail_in_id=int(mail_id),
            pec_inviata_id=None,
            pratica_numero=None,
            categoria=CAT_ALTRO,
            confidence=0.0,
            summary="Nessuna pratica corrispondente trovata.",
            action_required=False,
            key_facts={},
            ai_model="(skip-no-match)",
            ai_cost_eur=0.0,
            match_method=match.method,
            match_confidence=match.confidence,
        )
    else:
        result = classify(
            subject=mail.subject,
            sender=mail.sender,
            body=mail.body_text,
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            disabled=bool(settings.ai_disabled),
        )
        mail_repo.save_classification(
            mail_in_id=int(mail_id),
            pec_inviata_id=match.pec_inviata_id,
            pratica_numero=match.pratica_numero,
            categoria=result.categoria,
            confidence=result.confidence,
            summary=result.summary,
            action_required=result.action_required,
            key_facts=result.key_facts,
            ai_model=result.ai_model,
            ai_cost_eur=result.ai_cost_eur,
            match_method=match.method,
            match_confidence=match.confidence,
        )
        logger.info(
            "Riclassifica mail %s: categoria=%s conf=%.2f cost=%.4f EUR",
            mail_id, result.categoria, result.confidence, result.ai_cost_eur,
        )

    return RedirectResponse(url=f"/risposte/{mail_id}?riclassificata=1", status_code=303)


# --------------------------------------------------------------------------- #
#  Eliminazione
# --------------------------------------------------------------------------- #


@router.post("/risposte/{mail_id}/elimina")
def risposta_elimina(
    mail_id: int,
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> RedirectResponse:
    """Elimina mail_in + classificazione dal DB.

    Il file .eml su filesystem viene conservato. La mail non verrà riscaricata
    al prossimo polling se ha Message-ID valorizzato (UNIQUE INDEX).
    """
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")
    mail_repo.delete_mail(mail_id)
    logger.info("Mail %s eliminata dal cruscotto.", mail_id)
    return RedirectResponse(url="/risposte", status_code=303)
