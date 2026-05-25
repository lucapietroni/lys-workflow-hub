"""Pagine HTML per le bozze di risposta alle compagnie (M4).

Route esposte:

    GET  /bozze                          Lista bozze (filtrabile per stato)
    GET  /bozze/{draft_id}               Editor della bozza
    POST /bozze/{draft_id}/salva         Update body/allegati/destinatario
    POST /bozze/{draft_id}/invia         Invio PEC (dry-run da .env)
    POST /bozze/{draft_id}/annulla       Marca cancellata

    POST /risposte/{mail_id}/genera-bozza  Opt-in manuale (forza=True)
                                            Reindirizza all'editor della
                                            bozza appena creata.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.compagnie_repository import CompagnieRepository
from lys_workflow_hub.core.draft_repository import (
    CHANNEL_PEC,
    Draft,
    DraftAttachment,
    DraftRepository,
    STATI,
    STATUS_CANCELLED,
    STATUS_LABELS,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_SENT,
)
from lys_workflow_hub.core.mail_in_repository import (
    CATEGORIA_LABELS,
    MailRepository,
)
from lys_workflow_hub.core.pec_log_repository import PecLogRepository
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.workflows.risposte.context_builder import (
    build_scaffold_context,
)
from lys_workflow_hub.workflows.risposte.draft_service import (
    aggiorna_bozza,
    annulla_bozza,
    crea_bozza_se_serve,
    invia_bozza,
)
from lys_workflow_hub.workflows.risposte.sender import ParametriSpedizione


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["bozze"])


# --------------------------------------------------------------------------- #
#  Dependency wiring
# --------------------------------------------------------------------------- #


def get_draft_repo(
    settings: Settings = Depends(get_settings),
) -> DraftRepository:
    return DraftRepository(db_path=settings.app_db_path)


def get_mail_repo(
    settings: Settings = Depends(get_settings),
) -> MailRepository:
    return MailRepository(db_path=settings.app_db_path)


def get_compagnie_repo(
    settings: Settings = Depends(get_settings),
) -> CompagnieRepository:
    return CompagnieRepository(db_path=settings.app_db_path)


def get_pec_log_repo(
    settings: Settings = Depends(get_settings),
) -> PecLogRepository:
    return PecLogRepository(db_path=settings.app_db_path)


def get_wincar_repo() -> WinCarRepository | None:
    """WinCar e' opzionale: se non raggiungibile, le bozze restano comunque
    consultabili (perdiamo solo l'arricchimento context al re-generate)."""
    try:
        return WinCarRepository.from_settings()
    except Exception as exc:  # noqa: BLE001
        logger.warning("WinCarRepository non inizializzabile in route: %s", exc)
        return None


# --------------------------------------------------------------------------- #
#  Lista bozze
# --------------------------------------------------------------------------- #


@router.get("/bozze", response_class=HTMLResponse)
def bozze_list(
    request: Request,
    status: str | None = None,
    draft_repo: DraftRepository = Depends(get_draft_repo),
    mail_repo: MailRepository = Depends(get_mail_repo),
) -> HTMLResponse:
    """Lista bozze. Default: pending + ready (azionabili). Con
    `?status=sent` o `?status=cancelled` mostra anche le archiviate."""
    if status and status in STATI:
        drafts = draft_repo.list_by_status(status, limit=300)
        filtro = status
    else:
        drafts = (
            draft_repo.list_by_status(STATUS_PENDING, limit=200)
            + draft_repo.list_by_status(STATUS_READY, limit=200)
        )
        drafts.sort(key=lambda d: d.created_at, reverse=True)
        filtro = "azionabili"

    # Per ogni bozza, recupera la classificazione per mostrare la categoria.
    rows: list[dict] = []
    for d in drafts:
        classif = mail_repo.get_classification(d.mail_class_id)
        rows.append({
            "draft": d,
            "classif": classif,
            "categoria_label": (
                CATEGORIA_LABELS.get(classif.categoria, classif.categoria)
                if classif else "—"
            ),
        })

    counts = draft_repo.conta_per_status()

    return templates.TemplateResponse(
        request,
        "bozze_list.html",
        {
            "version": __version__,
            "rows": rows,
            "counts": counts,
            "filtro": filtro,
            "STATI": STATI,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_PENDING": STATUS_PENDING,
            "STATUS_READY": STATUS_READY,
            "STATUS_SENT": STATUS_SENT,
            "STATUS_CANCELLED": STATUS_CANCELLED,
            "totale": len(drafts),
        },
    )


# --------------------------------------------------------------------------- #
#  Preview / download di un allegato della bozza
# --------------------------------------------------------------------------- #


# Estensioni che il browser puo' renderizzare inline (no Content-Disposition).
_INLINE_EXT = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".txt": "text/plain; charset=utf-8",
}


def _mime_for(path: Path) -> str:
    return _INLINE_EXT.get(path.suffix.lower(), "application/octet-stream")


@router.get("/bozze/{draft_id}/allegato")
def bozza_allegato_preview(
    draft_id: int,
    path: str = Query(..., description="Path assoluto del file (deve corrispondere a un allegato della bozza)"),
    draft_repo: DraftRepository = Depends(get_draft_repo),
) -> FileResponse:
    """Serve uno degli allegati della bozza per anteprima nel browser.

    Sicurezza: il path richiesto deve corrispondere ESATTAMENTE a uno degli
    `attachments[].path` della bozza. Niente path traversal, niente file
    al di fuori della lista gia' associata alla bozza.
    """
    d = _load_draft_or_404(draft_id, draft_repo)
    valid_paths = {a.path for a in d.attachments}
    if path not in valid_paths:
        raise HTTPException(
            403,
            "Allegato non autorizzato per questa bozza.",
        )
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            410,
            f"File non piu' disponibile sul filesystem: {path}",
        )
    media_type = _mime_for(file_path)
    # Per i tipi inline (pdf, immagini), niente Content-Disposition: il
    # browser apre nella tab. Per gli altri, download diretto.
    is_inline = file_path.suffix.lower() in _INLINE_EXT
    headers = {}
    if is_inline:
        headers["Content-Disposition"] = f'inline; filename="{file_path.name}"'
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type=media_type,
        headers=headers,
    )


# --------------------------------------------------------------------------- #
#  Editor della singola bozza
# --------------------------------------------------------------------------- #


def _load_draft_or_404(
    draft_id: int, draft_repo: DraftRepository
) -> Draft:
    d = draft_repo.get_draft(draft_id)
    if d is None:
        raise HTTPException(404, f"Bozza id={draft_id} non trovata")
    return d


@router.get("/bozze/{draft_id}", response_class=HTMLResponse)
def bozza_edit(
    draft_id: int,
    request: Request,
    draft_repo: DraftRepository = Depends(get_draft_repo),
    mail_repo: MailRepository = Depends(get_mail_repo),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Editor della bozza.

    Mostra subject + body + checklist allegati + destinatario, e per le
    bozze gia' SENT/CANCELLED rende il form in sola lettura.
    """
    d = _load_draft_or_404(draft_id, draft_repo)
    classif = mail_repo.get_classification(d.mail_class_id)
    mail = mail_repo.get_mail(classif.mail_in_id) if classif else None

    return templates.TemplateResponse(
        request,
        "bozza_edit.html",
        {
            "version": __version__,
            "d": d,
            "classif": classif,
            "mail": mail,
            "categoria_label": (
                CATEGORIA_LABELS.get(classif.categoria, classif.categoria)
                if classif else "—"
            ),
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_PENDING": STATUS_PENDING,
            "STATUS_READY": STATUS_READY,
            "STATUS_SENT": STATUS_SENT,
            "STATUS_CANCELLED": STATUS_CANCELLED,
            "dry_run": bool(settings.pec_dry_run),
        },
    )


# --------------------------------------------------------------------------- #
#  Form action: salva (update)
# --------------------------------------------------------------------------- #


def _parse_attachments_form(
    current: Iterable[DraftAttachment],
    selected_paths: list[str],
) -> list[DraftAttachment]:
    """Rigenera la lista allegati impostando `included` in base ai checkbox.
    Mantiene `path` e `label` originali (l'utente non puo' modificarli da UI).
    """
    sel = {s.strip() for s in selected_paths if s and s.strip()}
    out: list[DraftAttachment] = []
    for a in current:
        out.append(DraftAttachment(
            path=a.path,
            label=a.label,
            included=a.path in sel,
        ))
    return out


@router.post("/bozze/{draft_id}/salva")
async def bozza_salva(
    draft_id: int,
    request: Request,
    mark_ready: bool = Form(False),
    draft_repo: DraftRepository = Depends(get_draft_repo),
) -> RedirectResponse:
    """Salva le modifiche dell'editor.

    Accetta form fields:
      - subject
      - body
      - to_address
      - allegati_inclusi[] (lista di path)
      - mark_ready (checkbox: porta a status READY)
    """
    d = _load_draft_or_404(draft_id, draft_repo)
    if not d.is_editable:
        raise HTTPException(
            409,
            f"Bozza in stato {d.status}: non modificabile",
        )

    form = await request.form()
    subject = (form.get("subject") or "").strip() or None
    body = (form.get("body") or "").strip() or None
    to_address = (form.get("to_address") or "").strip() or None
    allegati_inclusi = form.getlist("allegati_inclusi")

    new_attachments = _parse_attachments_form(d.attachments, allegati_inclusi)

    aggiorna_bozza(
        d.id,
        draft_repo=draft_repo,
        subject=subject,
        body_html=body,
        to_address=to_address,
        attachments=new_attachments,
        mark_ready=bool(mark_ready),
    )
    return RedirectResponse(url=f"/bozze/{d.id}", status_code=303)


# --------------------------------------------------------------------------- #
#  Form action: invia
# --------------------------------------------------------------------------- #


@router.post("/bozze/{draft_id}/invia", response_class=HTMLResponse)
async def bozza_invia(
    draft_id: int,
    request: Request,
    draft_repo: DraftRepository = Depends(get_draft_repo),
    mail_repo: MailRepository = Depends(get_mail_repo),
    pec_log_repo: PecLogRepository = Depends(get_pec_log_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    wincar_repo: WinCarRepository | None = Depends(get_wincar_repo),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Esegue l'invio PEC della bozza.

    Salva PRIMA i campi del form (cosi' eventuali modifiche dell'utente
    vengono prese in considerazione), poi chiama `invia_bozza` con i
    parametri SMTP/PEC da Settings. La modalita' dry-run e' governata da
    `settings.pec_dry_run`.
    """
    d = _load_draft_or_404(draft_id, draft_repo)
    if d.status == STATUS_CANCELLED:
        raise HTTPException(409, "Bozza annullata, non inviabile")

    # 1) Save first (best-effort sync of the form into the draft).
    if d.is_editable:
        form = await request.form()
        subject = (form.get("subject") or "").strip() or None
        body = (form.get("body") or "").strip() or None
        to_address = (form.get("to_address") or "").strip() or None
        allegati_inclusi = form.getlist("allegati_inclusi")
        new_attachments = _parse_attachments_form(d.attachments, allegati_inclusi)
        d = aggiorna_bozza(
            d.id,
            draft_repo=draft_repo,
            subject=subject,
            body_html=body,
            to_address=to_address,
            attachments=new_attachments,
            mark_ready=True,  # invio implica ready
        )

    # 2) Lookup compagnia dall'anagrafica per metadati invio.
    #    Usiamo build_scaffold_context (stessa logica della creazione bozza):
    #    legge il nome compagnia dalla pratica WinCar poi lo risolve in
    #    anagrafica. Piu' robusto del reverse-lookup per PEC perche' la
    #    compagnia puo' rispondere da un indirizzo diverso da c.pec.
    compagnia_nome = ""
    compagnia_id: int | None = None
    classif = mail_repo.get_classification(d.mail_class_id)
    if classif and classif.pratica_numero:
        try:
            ctx_meta = build_scaffold_context(
                pratica_numero=classif.pratica_numero,
                wincar_repo=wincar_repo,
                compagnie_repo=compagnie_repo,
                settings=settings,
            )
            compagnia_nome = ctx_meta.context.compagnia_nome
            if ctx_meta.compagnia is not None:
                compagnia_id = ctx_meta.compagnia.id
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lookup compagnia per invio bozza %s fallito: %s", d.id, exc)

    sender_email = (settings.pec_smtp_user or settings.carrozzeria_pec or "").strip()
    sender_display = (
        settings.carrozzeria_pec_alias
        or "Carrozzeria LYS Auto srl"
    )

    params = ParametriSpedizione(
        sender_email=sender_email,
        sender_display=sender_display,
        reply_to="",
        smtp_host=settings.pec_smtp_host,
        smtp_port=settings.pec_smtp_port,
        smtp_user=settings.pec_smtp_user,
        smtp_password=settings.pec_smtp_password,
        dry_run=bool(settings.pec_dry_run),
        archivio_root=Path(settings.app_archivio_pec) / "Risposte",
        compagnia_nome=compagnia_nome,
        compagnia_id=compagnia_id,
    )

    esito = invia_bozza(
        d.id,
        draft_repo=draft_repo,
        params=params,
        pec_log_repo=pec_log_repo,
    )

    return templates.TemplateResponse(
        request,
        "bozza_esito.html",
        {
            "version": __version__,
            "esito": esito,
            "d": esito.draft,
        },
    )


# --------------------------------------------------------------------------- #
#  Form action: annulla
# --------------------------------------------------------------------------- #


@router.post("/bozze/{draft_id}/annulla")
async def bozza_annulla(
    draft_id: int,
    request: Request,
    draft_repo: DraftRepository = Depends(get_draft_repo),
) -> RedirectResponse:
    """Marca la bozza come annullata. Solleva 409 se gia' SENT."""
    d = _load_draft_or_404(draft_id, draft_repo)
    if d.status == STATUS_SENT:
        raise HTTPException(409, "Bozza gia' inviata, non annullabile")
    form = await request.form()
    reason = (form.get("reason") or "").strip()
    annulla_bozza(d.id, draft_repo=draft_repo, reason=reason)
    return RedirectResponse(url="/bozze", status_code=303)


# --------------------------------------------------------------------------- #
#  Form action: genera-bozza opt-in da una mail M3
# --------------------------------------------------------------------------- #


@router.post("/risposte/{mail_id}/genera-bozza")
def risposta_genera_bozza(
    mail_id: int,
    request: Request,
    draft_repo: DraftRepository = Depends(get_draft_repo),
    mail_repo: MailRepository = Depends(get_mail_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    wincar_repo: WinCarRepository | None = Depends(get_wincar_repo),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Genera una bozza per una mail classificata, anche se la categoria
    sarebbe NESSUNA o OPT_IN (passa `forza=True`)."""
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        raise HTTPException(404, f"Mail id={mail_id} non trovata")
    classif = mail_repo.get_classification_for_mail(mail_id)
    if classif is None:
        raise HTTPException(
            409,
            "La mail non e' ancora classificata da M3: impossibile generare una bozza.",
        )

    ctx_meta = build_scaffold_context(
        pratica_numero=classif.pratica_numero,
        subject_originale=mail.subject,
        wincar_repo=wincar_repo,
        compagnie_repo=compagnie_repo,
        settings=settings,
    )

    draft = crea_bozza_se_serve(
        classif,
        draft_repo=draft_repo,
        mail_repo=mail_repo,
        scaffold_ctx=ctx_meta.context,
        archivio_root=settings.wincar_archivio,
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        ai_disabled=bool(settings.ai_disabled),
        to_address=mail.sender or "",
        forza=True,
    )
    if draft is None:
        # Caso teorico (forza=True dovrebbe sempre creare): difensivo.
        raise HTTPException(500, "Generazione bozza fallita")
    return RedirectResponse(url=f"/bozze/{draft.id}", status_code=303)
