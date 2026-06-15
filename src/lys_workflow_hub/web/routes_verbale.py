"""Routes per i verbali di consegna/riconsegna veicolo di cortesia.

GET  /pratiche/{numero}/verbale/uscita      → form pre-filled da WinCar
POST /pratiche/{numero}/verbale/uscita/pdf  → genera e scarica PDF
POST /pratiche/{numero}/verbale/uscita/salva → genera PDF + salva in WinCar → redirect
GET  /pratiche/{numero}/verbale/rientro     → form pre-filled da WinCar
POST /pratiche/{numero}/verbale/rientro/pdf → genera e scarica PDF
POST /pratiche/{numero}/verbale/rientro/salva → genera PDF + salva in WinCar → redirect
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.workflows.verbale_cortesia import (
    PdfConversionError,
    docx_bytes_to_pdf_bytes,
    filename_for,
    from_pratica,
    generate,
    list_verbali,
    save_verbale,
)
from lys_workflow_hub.workflows.verbale_cortesia.data import (
    TIPO_USCITA,
    TIPO_RIENTRO,
    LIVELLI_CARBURANTE,
)


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["verbale"])

PDF_MIME = "application/pdf"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _repo() -> WinCarRepository:
    return WinCarRepository.from_settings()


def _settings() -> Settings:
    return get_settings()


def _ctx() -> dict:
    return {"version": __version__}


def _form_to_overrides(form: dict) -> dict:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in form.items()}


def _build_data(numero: int, tipo: str, form: dict, repo: WinCarRepository):
    pratica = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, "Pratica non trovata.")
    overrides = _form_to_overrides(form)
    return pratica, from_pratica(pratica, tipo=tipo, overrides=overrides)


# ---------------------------------------------------------------------------
# Verbale Uscita
# ---------------------------------------------------------------------------


@router.get("/pratiche/{numero}/verbale/uscita", response_class=HTMLResponse)
def verbale_uscita_form(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
    settings: Settings = Depends(_settings),
) -> HTMLResponse:
    pratica = repo.get_pratica(numero)
    ctx = _ctx()
    ctx["numero"] = numero
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", ctx, status_code=404
        )
    data = from_pratica(pratica, tipo=TIPO_USCITA)
    ctx["pratica"] = pratica
    ctx["data"] = data
    ctx["livelli_carburante"] = LIVELLI_CARBURANTE
    try:
        ctx["verbali_archiviati"] = list_verbali(settings.wincar_archivio, numero)
    except Exception:
        ctx["verbali_archiviati"] = []
    return templates.TemplateResponse(request, "verbale_uscita.html", ctx)


@router.post("/pratiche/{numero}/verbale/uscita/pdf")
async def verbale_uscita_pdf(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
) -> Response:
    form = dict(await request.form())
    _, data = _build_data(numero, TIPO_USCITA, form, repo)
    docx_bytes = generate(data)
    try:
        pdf_bytes = docx_bytes_to_pdf_bytes(docx_bytes)
    except PdfConversionError as exc:
        raise HTTPException(503, str(exc)) from exc
    fname = filename_for(data).replace(".docx", ".pdf")
    return Response(
        content=pdf_bytes,
        media_type=PDF_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/verbale/uscita/salva")
async def verbale_uscita_salva(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
    settings: Settings = Depends(_settings),
) -> Response:
    form = dict(await request.form())
    _, data = _build_data(numero, TIPO_USCITA, form, repo)
    docx_bytes = generate(data)
    try:
        pdf_bytes = docx_bytes_to_pdf_bytes(docx_bytes)
    except PdfConversionError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        path = save_verbale(
            archivio_root=settings.wincar_archivio,
            numero_pratica=numero,
            tipo=TIPO_USCITA,
            pdf_bytes=pdf_bytes,
        )
    except (ValueError, OSError) as exc:
        logger.exception("Errore salvataggio verbale uscita pratica %s", numero)
        raise HTTPException(500, str(exc)) from exc
    return RedirectResponse(
        url=f"/pratiche/{numero}?verbale_salvato={path.name}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Verbale Rientro
# ---------------------------------------------------------------------------


@router.get("/pratiche/{numero}/verbale/rientro", response_class=HTMLResponse)
def verbale_rientro_form(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
    settings: Settings = Depends(_settings),
) -> HTMLResponse:
    pratica = repo.get_pratica(numero)
    ctx = _ctx()
    ctx["numero"] = numero
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", ctx, status_code=404
        )
    data = from_pratica(pratica, tipo=TIPO_RIENTRO)
    ctx["pratica"] = pratica
    ctx["data"] = data
    ctx["livelli_carburante"] = LIVELLI_CARBURANTE
    try:
        ctx["verbali_archiviati"] = list_verbali(settings.wincar_archivio, numero)
    except Exception:
        ctx["verbali_archiviati"] = []
    return templates.TemplateResponse(request, "verbale_rientro.html", ctx)


@router.post("/pratiche/{numero}/verbale/rientro/pdf")
async def verbale_rientro_pdf(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
) -> Response:
    form = dict(await request.form())
    _, data = _build_data(numero, TIPO_RIENTRO, form, repo)
    docx_bytes = generate(data)
    try:
        pdf_bytes = docx_bytes_to_pdf_bytes(docx_bytes)
    except PdfConversionError as exc:
        raise HTTPException(503, str(exc)) from exc
    fname = filename_for(data).replace(".docx", ".pdf")
    return Response(
        content=pdf_bytes,
        media_type=PDF_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/verbale/rientro/salva")
async def verbale_rientro_salva(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
    settings: Settings = Depends(_settings),
) -> Response:
    form = dict(await request.form())
    _, data = _build_data(numero, TIPO_RIENTRO, form, repo)
    docx_bytes = generate(data)
    try:
        pdf_bytes = docx_bytes_to_pdf_bytes(docx_bytes)
    except PdfConversionError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        path = save_verbale(
            archivio_root=settings.wincar_archivio,
            numero_pratica=numero,
            tipo=TIPO_RIENTRO,
            pdf_bytes=pdf_bytes,
        )
    except (ValueError, OSError) as exc:
        logger.exception("Errore salvataggio verbale rientro pratica %s", numero)
        raise HTTPException(500, str(exc)) from exc
    return RedirectResponse(
        url=f"/pratiche/{numero}?verbale_salvato={path.name}",
        status_code=303,
    )
