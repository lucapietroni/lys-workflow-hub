"""Pagine HTML server-side rendered con Jinja2.

Tre route principali:
    GET /                        Home + form di ricerca, con eventuale lista risultati
    GET /pratiche/{numero}       Dettaglio pratica
    GET /pratica/non-trovata     Pagina di errore amichevole (non e' un 404)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.core.wincar_repository import WinCarRepository


TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pages"])


def get_repository() -> WinCarRepository:
    return WinCarRepository.from_settings()


def _common_context() -> dict:
    return {"version": __version__}


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
