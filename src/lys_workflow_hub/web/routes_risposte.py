"""Pagine HTML per le risposte assicurative classificate (M3).

Route esposte:
    GET  /risposte                          Lista cronologica + filtri
    GET  /risposte/{mail_id}                Dettaglio risposta + classificazione AI
    GET  /risposte/{mail_id}/scarica        Download del .eml grezzo
    GET  /risposte/{mail_id}/allegati/{i}   Visualizza/scarica l'allegato i-esimo
    POST /risposte/{mail_id}/riclassifica   Re-estrae body+PDF e riclassifica con AI
    POST /risposte/{mail_id}/collega        Collega manualmente a una PEC inviata
    POST /risposte/{mail_id}/scollega       Rimuove collegamento PEC (torna a non matchata)
    POST /risposte/{mail_id}/ignora         Ignora mail singola dal tab "Da collegare"
    POST /risposte/ignora-non-matchate      Ignora tutte le mail non matchate (bulk)
    POST /risposte/{mail_id}/elimina        Elimina mail_in + classificazione dal DB
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
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
from lys_workflow_hub.core.pratica_stato_repository import PraticaStatoRepository
from lys_workflow_hub.integrations.ai_classifier import classify
from lys_workflow_hub.integrations.imap_fetcher import (
    get_attachment,
    list_attachments,
    reextract_body,
)
from lys_workflow_hub.workflows.risposte.matcher import match_mail
from lys_workflow_hub.web.auth import require_admin, template_context_processor


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["risposte"], dependencies=[Depends(require_admin)])


def get_mail_repo(
    settings: Settings = Depends(get_settings),
) -> MailRepository:
    return MailRepository(db_path=settings.app_db_path)


def get_pec_log_repo(
    settings: Settings = Depends(get_settings),
) -> PecLogRepository:
    return PecLogRepository(db_path=settings.app_db_path)


def get_stato_repo(
    settings: Settings = Depends(get_settings),
) -> PraticaStatoRepository:
    return PraticaStatoRepository(db_path=settings.app_db_path)


# --------------------------------------------------------------------------- #
#  Lista
# --------------------------------------------------------------------------- #


@router.get("/risposte", response_class=HTMLResponse)
def risposte_list(
    request: Request,
    tab: str = "matchate",
    categoria: str | None = None,
    only_action: bool = False,
    mail_repo: MailRepository = Depends(get_mail_repo),
    stato_repo: PraticaStatoRepository = Depends(get_stato_repo),
) -> HTMLResponse:
    non_matchate = tab == "non_matchate"
    records = mail_repo.list_con_classificazione(
        limit=300,
        solo_matched=(not non_matchate),
        solo_non_matchate=non_matchate,
    )
    if categoria and categoria in CATEGORIE:
        records = [r for r in records if r.categoria == categoria]
    if only_action and not non_matchate:
        records = [r for r in records if r.action_required]
    count_nm = mail_repo.count_non_matchate()
    # Batch-load stato corrente per ogni pratica unica nel tab matchate
    stati_pratiche: dict[int, object] = {}
    if not non_matchate:
        numeri = {r.pratica_numero for r in records if r.pratica_numero}
        for n in numeri:
            s = stato_repo.get_stato(n)
            if s:
                stati_pratiche[n] = s
    return templates.TemplateResponse(
        request,
        "risposte_list.html",
        {
            "version": __version__,
            "records": records,
            "tab": tab,
            "categoria": categoria,
            "only_action": only_action,
            "categorie": CATEGORIE,
            "categorie_labels": CATEGORIA_LABELS,
            "totale": len(records),
            "count_non_matchate": count_nm,
            "stati_pratiche": stati_pratiche,
        },
    )


@router.get("/risposte/{mail_id}", response_class=HTMLResponse)
def risposta_detail(
    mail_id: int,
    request: Request,
    cerca_pratica: str | None = None,
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
    cerca_pratica_int: int | None = None
    try:
        if cerca_pratica and cerca_pratica.strip():
            cerca_pratica_int = int(cerca_pratica.strip())
    except ValueError:
        pass
    pec_candidates: list = []
    if pec_inviata is None and cerca_pratica_int is not None:
        pec_candidates = pec_repo.list_by_pratica(cerca_pratica_int)
    allegati: list = []
    if mail.has_attachments and mail.raw_eml_path:
        try:
            raw = Path(mail.raw_eml_path).read_bytes()
            allegati = list_attachments(raw)
        except OSError as exc:
            logger.warning("Impossibile leggere .eml per allegati mail %s: %s", mail_id, exc)
    return templates.TemplateResponse(
        request,
        "risposta_detail.html",
        {
            "version": __version__,
            "mail": mail,
            "classificazione": classif,
            "pec_inviata": pec_inviata,
            "pec_candidates": pec_candidates,
            "cerca_pratica": cerca_pratica_int,
            "allegati": allegati,
        },
    )


@router.get("/risposte/{mail_id}/allegati/{index}")
def risposta_allegato(
    mail_id: int,
    index: int,
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> Response:
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")
    if not mail.raw_eml_path:
        raise HTTPException(404, "Mail senza .eml archiviato.")
    eml_path = Path(mail.raw_eml_path)
    if not eml_path.exists():
        raise HTTPException(404, "File .eml non trovato su disco.")
    result = get_attachment(eml_path.read_bytes(), index)
    if result is None:
        raise HTTPException(404, "Allegato non trovato.")
    content, filename, content_type = result
    return Response(
        content=content,
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
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
#  Collegamento manuale PEC
# --------------------------------------------------------------------------- #


@router.post("/risposte/{mail_id}/scollega")
def risposta_scollega(
    mail_id: int,
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> RedirectResponse:
    """Rimuove il collegamento PEC dalla classificazione (manuale o automatico)."""
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")
    classif = mail_repo.get_classification_for_mail(mail_id)
    if classif is None:
        raise HTTPException(400, "Nessuna classificazione da scollegare.")
    mail_repo.scollega_pec(mail_id)
    logger.info("Mail %s: collegamento PEC rimosso.", mail_id)
    return RedirectResponse(url=f"/risposte/{mail_id}?scollegata=1", status_code=303)


@router.post("/risposte/{mail_id}/collega")
def risposta_collega(
    mail_id: int,
    pec_inviata_id: int = Form(...),
    mail_repo: MailRepository = Depends(get_mail_repo),
    pec_repo: PecLogRepository = Depends(get_pec_log_repo),
) -> RedirectResponse:
    """Collega manualmente la risposta a una PEC inviata specifica."""
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")
    pec = pec_repo.get(pec_inviata_id)
    if pec is None:
        raise HTTPException(404, f"PEC id={pec_inviata_id} non trovata.")
    classif = mail_repo.get_classification_for_mail(mail_id)
    if classif is None:
        raise HTTPException(
            400, "Questa mail non ha ancora una classificazione. Esegui prima Riclassifica."
        )
    updated = mail_repo.aggiorna_link_pec(
        mail_in_id=mail_id,
        pec_inviata_id=pec_inviata_id,
        pratica_numero=pec.numero_pratica,
    )
    if not updated:
        raise HTTPException(500, "Aggiornamento classificazione fallito.")
    logger.info(
        "Mail %s collegata manualmente a PEC %s (pratica %s).",
        mail_id, pec_inviata_id, pec.numero_pratica,
    )
    return RedirectResponse(
        url=f"/risposte/{mail_id}?collegata=1", status_code=303
    )


# --------------------------------------------------------------------------- #
#  Ignora (dal tab "Da collegare")
# --------------------------------------------------------------------------- #


@router.post("/risposte/ignora-non-matchate")
def risposte_ignora_tutte(
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> RedirectResponse:
    """Soft-delete bulk di tutte le mail non matchate."""
    n = mail_repo.ignora_non_matchate()
    logger.info("Ignorate %d mail non matchate (bulk).", n)
    return RedirectResponse(url="/risposte?tab=non_matchate", status_code=303)


@router.post("/risposte/{mail_id}/ignora")
def risposta_ignora(
    mail_id: int,
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> RedirectResponse:
    """Soft-delete singola dal tab 'Da collegare'. Redirect al tab non_matchate."""
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")
    mail_repo.delete_mail(mail_id)
    logger.info("Mail %s ignorata dal tab non matchate.", mail_id)
    return RedirectResponse(url="/risposte?tab=non_matchate", status_code=303)


# --------------------------------------------------------------------------- #
#  Eliminazione
# --------------------------------------------------------------------------- #


@router.post("/risposte/{mail_id}/elimina")
def risposta_elimina(
    mail_id: int,
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> RedirectResponse:
    """Elimina classificazione e nasconde la mail (ignorata=1).

    Non fa hard-delete per evitare che il prossimo polling ri-scarichi la
    stessa mail (la riga tombstoned blocca il UNIQUE su uid_imap).
    """
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata.")
    mail_repo.hard_delete_mail(mail_id)
    logger.info("Mail %s eliminata dal DB.", mail_id)
    return RedirectResponse(url="/risposte?tab=non_matchate", status_code=303)
