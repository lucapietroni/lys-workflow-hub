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

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
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


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pages"])

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


# --------------------------------------------------------------------------- #
#  Home & dettaglio pratica
# --------------------------------------------------------------------------- #


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str | None = None,
    repo: WinCarRepository = Depends(get_repository),
) -> HTMLResponse:
    """Home: form di ricerca; se `q` e' valorizzato mostra anche i risultati.

    Logica di ricerca: se `q` e' numerico cerca per numero pratica; se ha 7
    caratteri e contiene una cifra cerca per targa; altrimenti per cognome.
    """
    context = _common_context()
    context["query"] = q or ""
    context["results"] = []
    context["search_kind"] = None

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
    return templates.TemplateResponse(request, "pratica_detail.html", context)


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
        elif key == "e_ditta":
            overrides[key] = str(raw).lower() in ("on", "true", "1", "yes")
        elif key in ("cedente_sesso",):
            overrides[key] = "F" if str(raw).upper() == "F" else "M"
        else:
            overrides[key] = (raw or "").strip() if isinstance(raw, str) else raw
    overrides.setdefault("e_ditta", False)
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
