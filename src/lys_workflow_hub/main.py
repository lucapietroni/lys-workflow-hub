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
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path


# -----------------------------------------------------------------------------
#  Fix per pythonw.exe (nessuna console disponibile)
# -----------------------------------------------------------------------------
# Quando l'app è lanciata con `pythonw.exe` (Task Scheduler, console nascosta),
# Python imposta `sys.stdout = sys.stderr = None`. Uvicorn ha un default
# StreamHandler su stderr e crasha subito con AttributeError. Anche il nostro
# logger lo farebbe. Redirigiamo entrambi su `os.devnull` PRIMA di qualsiasi
# altro import che possa toccarli: il logging vero va su file via
# RotatingFileHandler poco più sotto, quindi non perdiamo nulla.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", buffering=1, encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", buffering=1, encoding="utf-8")


from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from lys_workflow_hub import __version__
from lys_workflow_hub.config import get_settings
from lys_workflow_hub.core.schema_check import (
    SchemaCheckError,
    assert_schema_ok,
)
from lys_workflow_hub.core.utenti_repository import UtentiRepository
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.web.api import router as api_router
from lys_workflow_hub.web.auth import AuthMiddleware
from lys_workflow_hub.web.routes import router as pages_router
from lys_workflow_hub.web.routes_auth import router as auth_router
from lys_workflow_hub.web.routes_bozze import router as bozze_router
from lys_workflow_hub.web.routes_compagnie import router as compagnie_router
from lys_workflow_hub.web.routes_impostazioni import router as impostazioni_router
from lys_workflow_hub.web.routes_pec_log import router as pec_log_router
from lys_workflow_hub.web.routes_risposte import router as risposte_router
from lys_workflow_hub.web.routes_vandalismo import router as vandalismo_router
from lys_workflow_hub.web.routes_foto import router as foto_router
from lys_workflow_hub.web.routes_verbale import router as verbale_router


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


def _resolve_secret_key() -> str:
    """Chiave di firma della sessione (cookie di login).

    In produzione DEVE arrivare da `.env` (SECRET_KEY): senza, un riavvio
    dell'app invaliderebbe tutte le sessioni attive e — peggio — una chiave
    prevedibile/assente comprometterebbe l'autenticazione. In sviluppo, se
    non impostata, ne generiamo una effimera ad ogni avvio (comodo: nessuna
    config richiesta, costo: si viene disconnessi ad ogni riavvio del reload).
    """
    settings = get_settings()
    if settings.secret_key:
        return settings.secret_key
    if settings.app_env == "production":
        sys.stderr.write(
            "ERRORE: SECRET_KEY non impostata in .env. Obbligatoria in produzione "
            "(genera con: python -c \"import secrets; print(secrets.token_hex(32))\").\n"
        )
        sys.exit(2)
    logger.warning(
        "SECRET_KEY non impostata: uso una chiave effimera generata a runtime "
        "(le sessioni non sopravvivono al riavvio). Imposta SECRET_KEY in .env "
        "per avere sessioni stabili anche in sviluppo."
    )
    return secrets.token_hex(32)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Schema check al boot + avvio foto watcher. Vedi `core/schema_check.py`."""
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

    # Foto watcher (v2.1): avviato solo se FOTO_INBOX_PATH configurato in .env
    _foto_watcher = None
    if settings.foto_inbox_path:
        try:
            from lys_workflow_hub.core.foto_lavorazioni_repository import (
                FotoLavorazioniRepository,
            )
            from lys_workflow_hub.integrations.foto_watcher import FotoWatcher

            foto_repo = FotoLavorazioniRepository(db_path=settings.app_db_path)
            app.state.foto_repo = foto_repo  # condiviso con routes_foto per evitare DDL per-request
            _foto_watcher = FotoWatcher(settings=settings, foto_repo=foto_repo)
            _foto_watcher.start()
        except Exception:  # noqa: BLE001
            logger.exception("Impossibile avviare foto watcher — funzionalità disabilitata")

    yield

    if _foto_watcher:
        _foto_watcher.stop()
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

# Autenticazione (v3.0): SessionMiddleware firma il cookie, AuthMiddleware
# legge la sessione e blocca le route non pubbliche (vedi web/auth.py).
# L'ordine conta: `Starlette.add_middleware` INSERISCE in testa alla lista
# (non l'accoda), quindi il middleware aggiunto per ULTIMO finisce più
# ESTERNO e gira per primo su ogni richiesta. AuthMiddleware legge
# `request.session` prima di chiamare `call_next`, quindi ha bisogno che
# SessionMiddleware sia già stato eseguito -> SessionMiddleware deve essere
# più esterno di AuthMiddleware -> va aggiunto per ULTIMO.
_settings = get_settings()

# Singleton condiviso con AuthMiddleware e routes_auth.py (stesso pattern di
# app.state.foto_repo): evita di aprire/chiudere connessioni SQLite per ogni
# richiesta e permette ai test di sostituirlo con un DB temporaneo.
app.state.utenti_repo = UtentiRepository(
    db_path=_settings.app_db_path,
    max_attempts=_settings.login_max_attempts,
    lockout_minutes=_settings.login_lockout_minutes,
)

app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_resolve_secret_key(),
    max_age=_settings.session_max_age_days * 24 * 3600,
    same_site="lax",
    https_only=_settings.app_env == "production",
)

# Router: pagine HTML (root) e API JSON (/api).
app.include_router(auth_router)
app.include_router(pages_router)
app.include_router(compagnie_router)
app.include_router(vandalismo_router)
app.include_router(pec_log_router)
app.include_router(risposte_router)
app.include_router(bozze_router)
app.include_router(impostazioni_router)
app.include_router(verbale_router)
app.include_router(foto_router)
app.include_router(api_router)


@app.get("/health")
async def health() -> dict:
    """Sanity check di base, NON tocca WinCar."""
    return {"status": "ok", "version": __version__}


def main() -> None:
    """Avvio dell'app FastAPI tramite Uvicorn.

    In sviluppo abilita l'auto-reload limitato a `src/`. In produzione (env
    diverso da development) il reload è spento.
    """
    import uvicorn

    settings = get_settings()
    reload = settings.app_env == "development"
    src_dir = Path(__file__).resolve().parent.parent  # src/
    # log_config=None: NON lasciamo che uvicorn riconfiguri il logging.
    # Vogliamo che usi gli handler che abbiamo installato in _configura_logging
    # (RotatingFileHandler su disco), altrimenti rimette uno StreamHandler su
    # stderr che, sotto pythonw.exe, non sarebbe utile (e in dev raddoppia i log).
    uvicorn.run(
        "lys_workflow_hub.main:app",
        log_config=None,
        host=settings.app_host,
        port=settings.app_port,
        reload=reload,
        reload_dirs=[str(src_dir)] if reload else None,
    )


if __name__ == "__main__":
    main()
