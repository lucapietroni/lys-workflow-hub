"""Pagine HTML server-side rendered con Jinja2.

Route principali:
    GET  /                                  Home + form di ricerca
    GET  /pratiche/{numero}                 Dettaglio pratica (mostra anche scansioni firmate)
    GET  /pratiche/{numero}/cessione        Anteprima/edit dati cessione del credito
    POST /pratiche/{numero}/cessione        Genera e scarica il .docx
    POST /pratiche/{numero}/cessione/pdf    Genera e scarica il PDF
    POST /pratiche/{numero}/cessione/firmata Carica la scansione firmata e la archivia
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote as _urlquote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.draft_repository import (
    DraftRepository,
    STATUS_PENDING,
    STATUS_READY,
)
from lys_workflow_hub.core.sollecito_repository import SollecitoRepository
from lys_workflow_hub.core.mail_in_repository import MailRepository
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.pratica_files import Allegato, scan as scan_allegati
from lys_workflow_hub.core.pratica_stato_repository import (
    PraticaStatoRepository,
    STATI,
    STATO_LABELS,
)
from lys_workflow_hub.core.utenti_repository import Utente, UtentiRepository
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.workflows.cessione_credito import (
    PdfConversionError,
    docx_bytes_to_pdf_bytes,
    filename_for,
    from_pratica,
    generate,
    list_signed_pdfs,
    save_signed_pdf,
)
from lys_workflow_hub.workflows.verbale_cortesia.archive import list_verbali
from lys_workflow_hub.web.auth import require_admin, template_context_processor


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["pages"], dependencies=[Depends(require_admin)])

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
PDF_MIME = "application/pdf"


def get_repository() -> WinCarRepository:
    return WinCarRepository.from_settings()


def get_app_settings() -> Settings:
    return get_settings()


def _common_context() -> dict:
    return {"version": __version__}


# Estensioni renderizzabili inline nel browser (foto pratica + documenti).
_PREVIEW_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
}

# Formati immagine che nessun browser renderizza in <img> (niente thumbnail
# in griglia foto pratica, vanno mostrati come documento scaricabile).
_NON_RENDERIZZABILI = {".heic", ".heif"}


def _allegati_con_url(numero: int, items: list[Allegato]) -> list[dict[str, Any]]:
    """Arricchisce gli Allegato con l'URL di anteprima inline per il template."""
    return [
        {
            "nome_file": a.nome_file,
            "size_label": a.size_label,
            "data_modifica": a.data_modifica,
            "categoria": a.categoria,
            "url": f"/pratiche/{numero}/file?path={_urlquote(str(a.path))}",
        }
        for a in items
    ]


# --------------------------------------------------------------------------- #
#  Home & dettaglio pratica
# --------------------------------------------------------------------------- #


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str | None = None,
    repo: WinCarRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    """Home: form di ricerca; se `q` e' valorizzato mostra anche i risultati.

    Logica di ricerca: se `q` e' numerico cerca per numero pratica; se ha 7
    caratteri e contiene una cifra cerca per targa; altrimenti per cognome.
    """
    context = _common_context()
    context["query"] = q or ""
    context["results"] = []
    context["search_kind"] = None

    # KPI per la hero strip — silenzioso in caso di errore DB
    try:
        _draft_repo = DraftRepository(db_path=settings.app_db_path)
        _mail_repo = MailRepository(db_path=settings.app_db_path)
        _stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
        _sollecito_repo = SollecitoRepository(db_path=settings.app_db_path)
        _counts = _draft_repo.conta_per_status()
        context["kpi_bozze"] = (
            _counts.get(STATUS_PENDING, 0)
            + _counts.get(STATUS_READY, 0)
            + _sollecito_repo.conta_pending()
        )
        context["kpi_risposte_ar"] = _mail_repo.count_action_required()
        context["kpi_sla_breach"] = _stato_repo.count_sla_breach(
            sla_giorni=settings.sla_giorni_alert
        ) if settings.sla_giorni_alert > 0 else 0
    except Exception:
        context["kpi_bozze"] = 0
        context["kpi_risposte_ar"] = 0
        context["kpi_sla_breach"] = 0

    if q and q.strip():
        q_clean = q.strip()
        search_kind: str
        if q_clean.isdigit():
            search_kind = "numero"
            results = repo.search_pratiche(numero=int(q_clean), limit=20)
        elif len(q_clean) <= 8 and any(ch.isdigit() for ch in q_clean):
            search_kind = "targa"
            results = repo.search_pratiche(targa=q_clean, limit=20)
        else:
            search_kind = "cognome"
            results = repo.search_pratiche(cognome=q_clean, limit=20)
        context["results"] = results
        context["search_kind"] = search_kind

    return templates.TemplateResponse(request, "index.html", context)


@router.get("/pratiche/{numero}", response_class=HTMLResponse)
def pratica_detail(
    numero: int,
    request: Request,
    uploaded: str | None = None,
    repo: WinCarRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    pratica = repo.get_pratica(numero)
    context = _common_context()
    context["pratica"] = pratica
    context["numero"] = numero
    context["uploaded"] = uploaded
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", context, status_code=404
        )
    # Lista delle scansioni gia' firmate per questa pratica
    try:
        context["scansioni"] = list_signed_pdfs(settings.wincar_archivio, numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere scansioni archiviate per %s: %s", numero, exc)
        context["scansioni"] = []
    # M3: risposte assicurative da gestire per questa pratica.
    try:
        mail_repo = MailRepository(db_path=settings.app_db_path)
        context["risposte_da_gestire"] = mail_repo.list_action_required_per_pratica(numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere risposte M3 per %s: %s", numero, exc)
        context["risposte_da_gestire"] = []
    # M5: stato pratica + SLA alert.
    try:
        stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
        context["pratica_stato"] = stato_repo.get_stato(numero)
        context["pratica_stato_storia"] = stato_repo.storia(numero, limit=5)
        context["stati_disponibili"] = STATI
        context["stato_labels"] = STATO_LABELS
        # SLA alert: questa pratica ha PEC senza risposta oltre soglia?
        if settings.sla_giorni_alert > 0:
            sla_alerts = stato_repo.lista_sla_alerts(
                sla_giorni=settings.sla_giorni_alert
            )
            context["sla_breach_questa"] = [
                a for a in sla_alerts if a.pratica_numero == numero
            ]
        else:
            context["sla_breach_questa"] = []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile caricare stato/SLA pratica %s: %s", numero, exc)
        context["pratica_stato"] = None
        context["pratica_stato_storia"] = []
        context["stati_disponibili"] = STATI
        context["stato_labels"] = STATO_LABELS
        context["sla_breach_questa"] = []
    # Solleciti SLA per questa pratica (per mostrare se già inviato)
    try:
        sol_repo = SollecitoRepository(db_path=settings.app_db_path)
        context["solleciti_questa"] = sol_repo.list_per_pratica(numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile caricare solleciti per %s: %s", numero, exc)
        context["solleciti_questa"] = []
    # Parametro URL per conferma cambio stato
    context["stato_aggiornato"] = bool(request.query_params.get("stato_aggiornato"))
    # Verbali cortesia già generati per questa pratica
    try:
        context["verbali_cortesia"] = list_verbali(settings.wincar_archivio, numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere verbali cortesia per %s: %s", numero, exc)
        context["verbali_cortesia"] = []
    # Foto e documenti archiviati nelle cartelle Pubblici/Foto e Pubblici/Allegati
    try:
        allegati = scan_allegati(settings.wincar_archivio, numero)
        # HEIC/HEIF (iPhone): nessun browser le renderizza in <img>, quindi non
        # vanno in griglia come miniatura (thumbnail rotta e silenziosa) — le
        # trattiamo come documento (link diretto, non anteprima inline).
        foto_renderizzabili = [
            a for a in allegati.foto if a.path.suffix.lower() not in _NON_RENDERIZZABILI
        ]
        foto_non_renderizzabili = [
            a for a in allegati.foto if a.path.suffix.lower() in _NON_RENDERIZZABILI
        ]
        context["foto_pratica"] = _allegati_con_url(numero, foto_renderizzabili)
        context["documenti_pratica"] = _allegati_con_url(
            numero,
            allegati.cessioni + allegati.denunce + allegati.altri + foto_non_renderizzabili,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere foto/documenti per %s: %s", numero, exc)
        context["foto_pratica"] = []
        context["documenti_pratica"] = []
    # Collaboratori esterni assegnati a questa pratica (v3.0 fase 3)
    try:
        assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
        # Singleton condiviso con AuthMiddleware/routes_utenti.py, non una
        # connessione nuova — vedi app.state.utenti_repo in main.py.
        utenti_repo: UtentiRepository = request.app.state.utenti_repo
        assegnati_ids = set(assegnazioni_repo.list_utente_ids_per_pratica(numero))
        tutti_utenti = utenti_repo.list_all()
        context["collaboratori_assegnati"] = [
            u for u in tutti_utenti if u.id in assegnati_ids
        ]
        context["esterni_disponibili"] = [
            u for u in tutti_utenti
            if u.ruolo == "esterno" and u.attivo and u.id not in assegnati_ids
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere assegnazioni per %s: %s", numero, exc)
        context["collaboratori_assegnati"] = []
        context["esterni_disponibili"] = []
    return templates.TemplateResponse(request, "pratica_detail.html", context)


@router.post("/pratiche/{numero}/assegna")
def pratica_assegna(
    numero: int,
    utente_id: int = Form(...),
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    repo.assegna(numero, utente_id, assegnato_da=admin.id)
    return RedirectResponse(url=f"/pratiche/{numero}#collaboratori", status_code=303)


@router.post("/pratiche/{numero}/assegna/{utente_id}/rimuovi")
def pratica_rimuovi_assegnazione(
    numero: int,
    utente_id: int,
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    repo.rimuovi(numero, utente_id)
    return RedirectResponse(url=f"/pratiche/{numero}#collaboratori", status_code=303)


@router.get("/pratiche/{numero}/file")
def pratica_file_preview(
    numero: int,
    path: str = Query(..., description="Path assoluto del file (deve appartenere alla pratica)"),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    """Serve un file di Pubblici/Foto o Pubblici/Allegati per anteprima inline.

    Sicurezza: il path richiesto deve corrispondere ESATTAMENTE a uno dei file
    trovati dalla scansione delle cartelle di questa pratica. Niente path
    traversal, niente file al di fuori delle cartelle WinCar della pratica.
    """
    try:
        allegati = scan_allegati(settings.wincar_archivio, numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere allegati per %s: %s", numero, exc)
        raise HTTPException(404, "Pratica non trovata o cartelle non accessibili.")
    valid_paths = {str(a.path) for a in allegati.tutti}
    if path not in valid_paths:
        raise HTTPException(403, "File non autorizzato per questa pratica.")
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(410, f"File non piu' disponibile sul filesystem: {path}")
    ext = file_path.suffix.lower()
    # Solo i tipi che il browser sa renderizzare vanno inline (niente filename=,
    # header in minuscolo: evita che Starlette aggiunga un secondo
    # Content-Disposition: attachment, vedi bozza_allegato_preview). Gli altri
    # (.docx, .xlsx, .heic, ecc.) restano attachment col comportamento di default.
    if ext in _PREVIEW_MIME:
        return FileResponse(
            path=file_path,
            media_type=_PREVIEW_MIME[ext],
            headers={"content-disposition": f'inline; filename="{file_path.name}"'},
        )
    return FileResponse(path=file_path, filename=file_path.name)


# --------------------------------------------------------------------------- #
#  Workflow A — Cessione del credito
# --------------------------------------------------------------------------- #


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _build_overrides(form: dict[str, Any]) -> dict[str, Any]:
    """Converte il dizionario form HTML in override tipizzati per `from_pratica`."""
    overrides: dict[str, Any] = {}
    for key, raw in form.items():
        if key in ("cedente_data_nascita", "sinistro_data"):
            overrides[key] = _parse_date(raw)
        elif key in ("e_ditta", "e_vandalismo"):
            overrides[key] = str(raw).lower() in ("on", "true", "1", "yes")
        elif key in ("cedente_sesso",):
            overrides[key] = "F" if str(raw).upper() == "F" else "M"
        else:
            overrides[key] = (raw or "").strip() if isinstance(raw, str) else raw
    overrides.setdefault("e_ditta", False)
    overrides.setdefault("e_vandalismo", False)
    return overrides


@router.get("/pratiche/{numero}/cessione", response_class=HTMLResponse)
def cessione_preview(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_repository),
) -> HTMLResponse:
    """Anteprima editabile della cessione del credito."""
    pratica = repo.get_pratica(numero)
    context = _common_context()
    context["numero"] = numero
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", context, status_code=404
        )
    data = from_pratica(pratica)
    context["pratica"] = pratica
    context["data"] = data
    context["mancanti"] = data.campi_mancanti()
    return templates.TemplateResponse(request, "cessione_preview.html", context)


def _build_cessione_data(numero: int, form: dict[str, Any], repo: WinCarRepository):
    pratica = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, "Pratica non trovata.")
    overrides = _build_overrides(form)
    return pratica, from_pratica(pratica, overrides=overrides)


@router.post("/pratiche/{numero}/cessione")
async def cessione_generate_docx(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_repository),
) -> Response:
    """Genera e scarica il .docx di cessione del credito."""
    form = await request.form()
    _, data = _build_cessione_data(numero, dict(form), repo)
    docx_bytes = generate(data)
    fname = filename_for(data)
    return Response(
        content=docx_bytes,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/cessione/pdf")
async def cessione_generate_pdf(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_repository),
) -> Response:
    """Genera e scarica il PDF di cessione del credito (per stampa)."""
    form = await request.form()
    _, data = _build_cessione_data(numero, dict(form), repo)
    docx_bytes = generate(data)
    try:
        pdf_bytes = docx_bytes_to_pdf_bytes(docx_bytes)
    except PdfConversionError as exc:
        # Errore esplicito al browser: l'utente vede il messaggio e capisce cosa fare.
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    fname = filename_for(data).replace(".docx", ".pdf")
    return Response(
        content=pdf_bytes,
        media_type=PDF_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/cessione/firmata")
async def cessione_upload_signed(
    numero: int,
    file: UploadFile = File(...),
    repo: WinCarRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    """Carica la scansione firmata e la salva in Pratiche/<n>/Privati/."""
    pratica = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, "Pratica non trovata.")
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            400,
            f"Tipo file non supportato: {file.content_type}. Accettati solo PDF.",
        )
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(400, "File troppo grande (max 20 MB).")

    try:
        result = save_signed_pdf(
            archivio_root=settings.wincar_archivio,
            numero_pratica=numero,
            pdf_bytes=raw,
            central_archive_root=settings.app_archivio_cessioni or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        logger.exception("Errore di filesystem nel salvataggio scansione")
        raise HTTPException(500, f"Errore di filesystem: {exc}") from exc

    # Torniamo alla pagina dettaglio con un parametro che fa apparire il banner.
    return RedirectResponse(
        url=f"/pratiche/{numero}?uploaded={result.pratica_path.name}",
        status_code=303,
    )
