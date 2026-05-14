"""Entry point dell'app web (FastAPI + Uvicorn).

Avvio in sviluppo:
    python -m lys_workflow_hub.main

Avvio in produzione (Windows service via NSSM):
    uvicorn lys_workflow_hub.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from lys_workflow_hub import __version__
from lys_workflow_hub.config import get_settings
from lys_workflow_hub.core.schema_check import (
    SchemaCheckError,
    assert_schema_ok,
)
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.web.api import router as api_router
from lys_workflow_hub.web.routes import router as pages_router
from lys_workflow_hub.web.routes_compagnie import router as compagnie_router
from lys_workflow_hub.web.routes_pec_log import router as pec_log_router
from lys_workflow_hub.web.routes_vandalismo import router as vandalismo_router


logger = logging.getLogger("lys_workflow_hub")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Schema check al boot. Vedi `core/schema_check.py`."""
    settings = get_settings()
    logger.info("LYS Workflow Hub v%s - env=%s", __version__, settings.app_env)
    logger.info("WinCar archivio: %s", settings.wincar_archivio)

    try:
        repo = WinCarRepository.from_settings(settings)
        result = assert_schema_ok(repo)
        logger.info(result.explain())
    except SchemaCheckError as exc:
        logger.error("Schema check fallito:\n%s", exc)
        if settings.app_env == "production":
            sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Schema check non eseguito (WinCar non raggiungibile?): %s", exc)

    yield
    logger.info("LYS Workflow Hub: shutdown")


app = FastAPI(
    title="LYS Workflow Hub",
    version=__version__,
    description="Piattaforma di automazione documentale per Carrozzeria LYS Auto srl.",
    lifespan=lifespan,
)

# Mount file statici (CSS, eventualmente JS / immagini in futuro).
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Router: pagine HTML (root) e API JSON (/api).
app.include_router(pages_router)
app.include_router(compagnie_router)
app.include_router(vandalismo_router)
app.include_router(pec_log_router)
app.include_router(api_router)


@app.get("/health")
async def health() -> dict:
    """Sanity check di base, NON tocca WinCar."""
    return {"status": "ok", "version": __version__}


def main() -> None:
    """Avvio in modalita' sviluppo con auto-reload limitato a `src/`.

    Il default di uvicorn osserva l'intera cwd inclusi `.venv\\Lib\\site-packages`,
    cosa che su Windows + OneDrive fa scattare reload in loop. Limitiamo il
    watcher alla cartella sorgente del nostro pacchetto.
    """
    import uvicorn

    settings = get_settings()
    reload = settings.app_env == "development"
    src_dir = Path(__file__).resolve().parent.parent  # src/
    uvicorn.run(
        "lys_workflow_hub.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=reload,
        reload_dirs=[str(src_dir)] if reload else None,
    )


if __name__ == "__main__":
    main()
