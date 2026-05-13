"""Pagine HTML server-side rendered con Jinja2.

Route principali:
    GET  /                              Home + form di ricerca, con eventuale lista risultati
    GET  /pratiche/{numero}             Dettaglio pratica
    GET  /pratiche/{numero}/cessione    Anteprima/edit dati cessione del credito
    POST /pratiche/{numero}/cessione    Genera e scarica il .docx
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.workflows.cessione_credito import (
    filename_for,
    from_pratica,
    generate,
)


TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pages"])

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def get_repository() -> WinCarRepository:
    return WinCarRepository.from_settings()


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
    repo: WinCarRepository = Depends(get_repository),
) -> HTMLResponse:
    pratica = repo.get_pratica(numero)
    context = _common_context()
    context["pratica"] = pratica
    context["numero"] = numero
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", context, status_code=404
        )
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
    # se la checkbox e_ditta non e' stata inviata, FastAPI non la includera':
    # in tal caso la trattiamo come False solo se compaiono altri campi ditta.
    overrides.setdefault("e_ditta", False)
    return overrides


@router.get("/pratiche/{numero}/cessione", response_class=HTMLResponse)
def cessione_preview(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_repository),
) -> HTMLResponse:
    """Anteprima editabile della cessione del credito.

    Mostra un form pre-compilato con i dati estratti da WinCar e derivati dal
    codice fiscale. L'operatore puo' correggere o completare i campi (in
    particolare la dinamica del sinistro) prima di generare il documento.
    """
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


@router.post("/pratiche/{numero}/cessione")
async def cessione_generate(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_repository),
) -> Response:
    """Genera e scarica il documento .docx di cessione del credito."""
    pratica = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, "Pratica non trovata.")
    form = await request.form()
    overrides = _build_overrides(dict(form))
    data = from_pratica(pratica, overrides=overrides)
    docx_bytes = generate(data)
    fname = filename_for(data)
    return Response(
        content=docx_bytes,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
