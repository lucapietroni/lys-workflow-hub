"""Route /foto — stato watcher e log foto lavorazioni processate."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import get_settings
from lys_workflow_hub.core.foto_lavorazioni_repository import FotoLavorazioniRepository
from lys_workflow_hub.web.auth import require_admin, template_context_processor

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["foto"], dependencies=[Depends(require_admin)])


@router.get("/foto", response_class=HTMLResponse)
def foto_inbox(request: Request) -> HTMLResponse:
    settings = get_settings()
    # Usa il repo singleton creato in lifespan (app.state.foto_repo) se disponibile,
    # altrimenti ne crea uno temporaneo (fallback per dev senza watcher avviato).
    foto_repo: FotoLavorazioniRepository = getattr(
        request.app.state, "foto_repo", None
    ) or FotoLavorazioniRepository(db_path=settings.app_db_path)
    records = foto_repo.list_recenti(limit=100)
    inbox_path = settings.foto_inbox_path
    inbox_files: list[str] = []
    if inbox_path:
        inbox = Path(inbox_path)
        inbox_files = [f.name for f in sorted(inbox.iterdir()) if f.is_file()] if inbox.exists() else []
    ctx = {
        "version": __version__,
        "records": records,
        "inbox_path": str(inbox_path) if inbox_path else "(non configurato)",
        "fallback_path": str(settings.foto_fallback_path),
        "inbox_files": inbox_files,
        "copia_pratica_abilitata": foto_repo.get_copia_pratica_abilitata(),
    }
    return templates.TemplateResponse(request, "foto_inbox.html", ctx)


@router.post("/foto/copia-pratica")
def toggle_copia_pratica(request: Request) -> RedirectResponse:
    settings = get_settings()
    foto_repo: FotoLavorazioniRepository = getattr(
        request.app.state, "foto_repo", None
    ) or FotoLavorazioniRepository(db_path=settings.app_db_path)
    nuovo_stato = not foto_repo.get_copia_pratica_abilitata()
    foto_repo.set_copia_pratica_abilitata(nuovo_stato)
    return RedirectResponse(url="/foto", status_code=303)
