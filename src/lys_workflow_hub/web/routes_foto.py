"""Route /foto — stato watcher e log foto lavorazioni processate."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.foto_lavorazioni_repository import FotoLavorazioniRepository

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["foto"])


def _settings() -> Settings:
    return get_settings()


def _foto_repo(settings: Settings = Depends(_settings)) -> FotoLavorazioniRepository:
    return FotoLavorazioniRepository(db_path=settings.app_db_path)


@router.get("/foto", response_class=HTMLResponse)
def foto_inbox(
    request: Request,
    settings: Settings = Depends(_settings),
    foto_repo: FotoLavorazioniRepository = Depends(_foto_repo),
) -> HTMLResponse:
    records = foto_repo.list_recenti(limit=100)
    inbox = Path(settings.foto_inbox_path)
    inbox_files = sorted(inbox.iterdir()) if inbox.exists() else []
    ctx = {
        "version": __version__,
        "records": records,
        "inbox_path": str(settings.foto_inbox_path),
        "fallback_path": str(settings.foto_fallback_path),
        "inbox_files": [f.name for f in inbox_files if f.is_file()],
    }
    return templates.TemplateResponse(request, "foto_inbox.html", ctx)
