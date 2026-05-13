"""Entry point dell'app web (FastAPI + Uvicorn).

Avvio in sviluppo:
    python -m lys_workflow_hub.main

Avvio in produzione (Windows service via NSSM):
    uvicorn lys_workflow_hub.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from lys_workflow_hub import __version__
from lys_workflow_hub.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Eventi di startup/shutdown.

    In startup vorremo:
    - eseguire lo schema-check su WinCar e bloccare l'app se le colonne attese mancano;
    - aprire il pool di connessioni ODBC;
    - inizializzare il DB interno SQLite.

    Per ora è un placeholder.
    """
    settings = get_settings()
    print(f"[startup] LYS Workflow Hub v{__version__} env={settings.app_env}")
    print(f"[startup] WinCar archivio: {settings.wincar_archivio}")
    yield
    print("[shutdown] arrivederci")


app = FastAPI(
    title="LYS Workflow Hub",
    version=__version__,
    description="Piattaforma di automazione documentale per Carrozzeria LYS Auto srl.",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    """Pagina di benvenuto provvisoria."""
    return f"""
    <!doctype html>
    <html lang="it">
      <head><meta charset="utf-8"><title>LYS Workflow Hub</title></head>
      <body style="font-family: system-ui; max-width: 720px; margin: 4rem auto; color: #1f3a5f">
        <h1>LYS Workflow Hub</h1>
        <p>Versione {__version__} — scheletro iniziale.</p>
        <p>Endpoint utili:</p>
        <ul>
          <li><a href="/health">/health</a></li>
          <li><a href="/docs">/docs</a> (OpenAPI)</li>
        </ul>
      </body>
    </html>
    """


@app.get("/health")
async def health() -> dict:
    """Sanity check di base."""
    return {"status": "ok", "version": __version__}


def main() -> None:
    """Avvio in modalità sviluppo con auto-reload."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "lys_workflow_hub.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=(settings.app_env == "development"),
    )


if __name__ == "__main__":
    main()
