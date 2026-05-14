"""Entry point dell'app web (FastAPI + Uvicorn).

Avvio in sviluppo (con auto-reload, log su terminale + file):
    python -m lys_workflow_hub.main

Avvio in produzione (Task Scheduler, console nascosta, log su file):
    .venv\\Scripts\\pythonw.exe -m lys_workflow_hub.main

Avvio in produzione (Windows service via NSSM):
    uvicorn lys_workflow_hub.main:app --host 0.0.0.0 --port 8000

I log finiscono sempre nel file configurato in `APP_LOG_PATH` (default
`C:\\LYSApp\\logs\\lys-hub.log`) con rotazione automatica (5 MB x 5 file).
Quando `python.exe` è in uso, vanno anche su stdout/stderr.
"""
from __future__ import annotations

import logging
import logging.handlers
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


# -----------------------------------------------------------------------------
#  Logging
# -----------------------------------------------------------------------------


def _configura_logging() -> None:
    """Configura logging dell'app + dei logger uvicorn.

    Output simultaneo su:
      - stdout (`StreamHandler`) — visibile solo se l'app è lanciata con
        `python.exe` (terminale attivo). Inutile ma innocuo con `pythonw.exe`.
      - file con rotazione (`RotatingFileHandler`) — sempre attivo. È il modo
        per leggere cosa fa l'app quando gira in background.

    Cattura anche i logger di uvicorn/uvicorn.error/uvicorn.access in modo che
    le richieste HTTP e gli errori del server finiscano nello stesso file.
    """
    settings = get_settings()
    level_name = (settings.app_log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handlers: list[logging.Handler] = []

    # File handler con rotazione (5 MB x 5 backup). La cartella viene creata
    # se non esiste. In caso di errore (es. permessi), fallback su stderr.
    try:
        log_path = Path(settings.app_log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        handlers.append(file_handler)
    except OSError as exc:
        sys.stderr.write(
            f"WARN: impossibile aprire file di log {settings.app_log_path}: {exc}\n"
        )

    # Stream handler su stdout (visibile in dev / con python.exe).
    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(fmt)
    handlers.append(stream_handler)

    # Root logger: cancello eventuali handler default e installo i nostri.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)

    # Uvicorn ha logger nominati: agganciamoli al root (propagate=True) e
    # togliamo i loro handler default per evitare doppi log.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        for h in list(uv_logger.handlers):
            uv_logger.removeHandler(h)
        uv_logger.propagate = True
        uv_logger.setLevel(level)


_configura_logging()
logger = logging.getLogger("lys_workflow_hub")

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
