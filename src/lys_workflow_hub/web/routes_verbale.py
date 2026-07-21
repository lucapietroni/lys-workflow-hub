"""Routes per i verbali di consegna/riconsegna veicolo di cortesia.

GET  /pratiche/{numero}/verbale/uscita          → form (auto cortesia dropdown)
POST /pratiche/{numero}/verbale/uscita/docx     → genera e scarica .docx + log DB
POST /pratiche/{numero}/verbale/uscita/pdf      → genera e scarica PDF per stampa + log DB
POST /pratiche/{numero}/verbale/uscita/firmata  → carica PDF firmato → archivia in WinCar
GET  /pratiche/{numero}/verbale/rientro         → form (pre-fill da ultimo uscita)
POST /pratiche/{numero}/verbale/rientro/docx    → genera e scarica .docx + log DB
POST /pratiche/{numero}/verbale/rientro/pdf     → genera e scarica PDF per stampa + log DB
POST /pratiche/{numero}/verbale/rientro/firmata → carica PDF firmato → archivia in WinCar
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.auto_cortesia_repository import AutoCortesiaRepository
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.workflows.verbale_cortesia import (
    PdfConversionError,
    VerbaleData,
    docx_bytes_to_pdf_bytes,
    filename_for,
    from_pratica,
    generate,
    list_verbali,
    save_verbale,
)
from lys_workflow_hub.workflows.verbale_cortesia.data import (
    LIVELLI_CARBURANTE,
    TIPO_RIENTRO,
    TIPO_USCITA,
)
from lys_workflow_hub.web.auth import require_admin, template_context_processor


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["verbale"], dependencies=[Depends(require_admin)])

PDF_MIME  = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _repo() -> WinCarRepository:
    return WinCarRepository.from_settings()


def _settings() -> Settings:
    return get_settings()


def _auto_repo(settings: Settings = Depends(_settings)) -> AutoCortesiaRepository:
    return AutoCortesiaRepository(db_path=settings.app_db_path)


def _ctx() -> dict:
    return {"version": __version__}


def _form_to_overrides(form: dict) -> dict:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in form.items()}


def _parse_auto_id(form: dict) -> int | None:
    try:
        val = int(form.get("auto_id") or 0)
        return val or None
    except (ValueError, TypeError):
        return None


def _build_data(
    numero: int,
    tipo: str,
    form: dict,
    repo: WinCarRepository,
    auto_repo: AutoCortesiaRepository,
):
    pratica = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, "Pratica non trovata.")
    overrides = _form_to_overrides(form)
    auto_id = _parse_auto_id(overrides)
    auto = auto_repo.get_auto(auto_id) if auto_id else None
    data = from_pratica(pratica, tipo=tipo, auto=auto, overrides=overrides)
    return pratica, data, auto


def _last_rientro_map(auto_repo: AutoCortesiaRepository, autos) -> str:
    """JSON dict {auto_id: {km, danni}} — da iniettare in template per uscita."""
    result: dict[str, dict] = {}
    for a in autos:
        lr = auto_repo.get_last_rientro(a.id)
        if lr:
            result[str(a.id)] = {
                "km": lr.km,
                "danni": [list(d) for d in lr.danni],
            }
    return json.dumps(result, ensure_ascii=False)


def _log_verbale_db(data: VerbaleData, auto_repo: AutoCortesiaRepository,
                    numero: int) -> None:
    if not data.auto_id:
        return
    try:
        auto_repo.save_verbale(
            tipo=data.tipo,
            auto_id=data.auto_id,
            pratica_numero=numero,
            km=data.km,
            livello_carburante=data.livello_carburante,
            danni=list(data.danni),
            note=data.note,
            data_ora=data.data_ora,
        )
    except Exception:
        logger.exception("Errore log verbale %s DB per auto %s", data.tipo, data.auto_id)


# ---------------------------------------------------------------------------
# Verbale Uscita
# ---------------------------------------------------------------------------


@router.get("/pratiche/{numero}/verbale/uscita", response_class=HTMLResponse)
def verbale_uscita_form(
    numero: int,
    request: Request,
    firmata: str | None = None,
    repo: WinCarRepository = Depends(_repo),
    settings: Settings = Depends(_settings),
    auto_repo: AutoCortesiaRepository = Depends(_auto_repo),
) -> HTMLResponse:
    pratica = repo.get_pratica(numero)
    ctx = _ctx()
    ctx["numero"] = numero
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", ctx, status_code=404
        )
    autos = auto_repo.list_auto()
    data = from_pratica(pratica, tipo=TIPO_USCITA)
    ctx["pratica"] = pratica
    ctx["data"] = data
    ctx["autos"] = autos
    ctx["livelli_carburante"] = LIVELLI_CARBURANTE
    ctx["last_rientro_map_json"] = _last_rientro_map(auto_repo, autos)
    ctx["firmata"] = bool(firmata)
    try:
        ctx["verbali_archiviati"] = list_verbali(settings.wincar_archivio, numero)
    except Exception:
        ctx["verbali_archiviati"] = []
    return templates.TemplateResponse(request, "verbale_uscita.html", ctx)


@router.post("/pratiche/{numero}/verbale/uscita/docx")
async def verbale_uscita_docx(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
    auto_repo: AutoCortesiaRepository = Depends(_auto_repo),
) -> Response:
    form = dict(await request.form())
    _, data, _ = _build_data(numero, TIPO_USCITA, form, repo, auto_repo)
    docx_bytes = generate(data)
    _log_verbale_db(data, auto_repo, numero)
    fname = filename_for(data)
    return Response(
        content=docx_bytes,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/verbale/uscita/pdf")
async def verbale_uscita_pdf(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
    auto_repo: AutoCortesiaRepository = Depends(_auto_repo),
) -> Response:
    form = dict(await request.form())
    _, data, _ = _build_data(numero, TIPO_USCITA, form, repo, auto_repo)
    docx_bytes = generate(data)
    try:
        pdf_bytes = docx_bytes_to_pdf_bytes(docx_bytes)
    except PdfConversionError as exc:
        raise HTTPException(503, str(exc)) from exc
    _log_verbale_db(data, auto_repo, numero)
    fname = filename_for(data).replace(".docx", ".pdf")
    return Response(
        content=pdf_bytes,
        media_type=PDF_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/verbale/uscita/firmata")
async def verbale_uscita_firmata(
    numero: int,
    file: UploadFile = File(...),
    settings: Settings = Depends(_settings),
) -> RedirectResponse:
    raw = await file.read()
    try:
        save_verbale(
            archivio_root=settings.wincar_archivio,
            numero_pratica=numero,
            tipo=TIPO_USCITA,
            pdf_bytes=raw,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(
        url=f"/pratiche/{numero}/verbale/uscita?firmata=1",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Verbale Rientro
# ---------------------------------------------------------------------------


@router.get("/pratiche/{numero}/verbale/rientro", response_class=HTMLResponse)
def verbale_rientro_form(
    numero: int,
    request: Request,
    firmata: str | None = None,
    repo: WinCarRepository = Depends(_repo),
    settings: Settings = Depends(_settings),
    auto_repo: AutoCortesiaRepository = Depends(_auto_repo),
) -> HTMLResponse:
    pratica = repo.get_pratica(numero)
    ctx = _ctx()
    ctx["numero"] = numero
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", ctx, status_code=404
        )
    autos = auto_repo.list_auto()
    # Pre-fill auto dall'ultimo verbale di uscita per questa pratica
    last_uscita = auto_repo.get_last_uscita_for_pratica(numero)
    auto_from_uscita = auto_repo.get_auto(last_uscita.auto_id) if last_uscita else None
    data = from_pratica(pratica, tipo=TIPO_RIENTRO, auto=auto_from_uscita)
    ctx["pratica"] = pratica
    ctx["data"] = data
    ctx["autos"] = autos
    ctx["livelli_carburante"] = LIVELLI_CARBURANTE
    ctx["firmata"] = bool(firmata)
    try:
        ctx["verbali_archiviati"] = list_verbali(settings.wincar_archivio, numero)
    except Exception:
        ctx["verbali_archiviati"] = []
    return templates.TemplateResponse(request, "verbale_rientro.html", ctx)


@router.post("/pratiche/{numero}/verbale/rientro/docx")
async def verbale_rientro_docx(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
    auto_repo: AutoCortesiaRepository = Depends(_auto_repo),
) -> Response:
    form = dict(await request.form())
    _, data, _ = _build_data(numero, TIPO_RIENTRO, form, repo, auto_repo)
    docx_bytes = generate(data)
    _log_verbale_db(data, auto_repo, numero)
    fname = filename_for(data)
    return Response(
        content=docx_bytes,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/verbale/rientro/pdf")
async def verbale_rientro_pdf(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(_repo),
    auto_repo: AutoCortesiaRepository = Depends(_auto_repo),
) -> Response:
    form = dict(await request.form())
    _, data, _ = _build_data(numero, TIPO_RIENTRO, form, repo, auto_repo)
    docx_bytes = generate(data)
    try:
        pdf_bytes = docx_bytes_to_pdf_bytes(docx_bytes)
    except PdfConversionError as exc:
        raise HTTPException(503, str(exc)) from exc
    _log_verbale_db(data, auto_repo, numero)
    fname = filename_for(data).replace(".docx", ".pdf")
    return Response(
        content=pdf_bytes,
        media_type=PDF_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/verbale/rientro/firmata")
async def verbale_rientro_firmata(
    numero: int,
    file: UploadFile = File(...),
    settings: Settings = Depends(_settings),
) -> RedirectResponse:
    raw = await file.read()
    try:
        save_verbale(
            archivio_root=settings.wincar_archivio,
            numero_pratica=numero,
            tipo=TIPO_RIENTRO,
            pdf_bytes=raw,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(
        url=f"/pratiche/{numero}/verbale/rientro?firmata=1",
        status_code=303,
    )
