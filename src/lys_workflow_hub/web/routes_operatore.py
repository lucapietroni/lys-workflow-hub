"""Ingressi officina lato operatore — creazione bozza pratica prima che
esista in WinCar (ruolo "operatore").

Nessuna `dependencies=[Depends(require_admin)]` qui: stesso pattern di
`routes_portale.py`, protetto solo da `AuthMiddleware` (serve essere
loggati), con un guard locale (`_richiedi_operatore`) che nega l'accesso a
chiunque non abbia esattamente `ruolo == "operatore"` — un operatore non
vede pratiche esistenti, un admin/esterno non vede questa pagina.

Il collegamento a un numero pratica WinCar reale (e lo spostamento dei
file in `Pratiche/<numero>/...`) avviene solo lato admin, vedi
`web/routes_ingressi.py`.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.ingressi_officina_repository import (
    STATO_IN_ATTESA,
    TIPI_FILE,
    TIPO_FILE_LABELS,
    IngressiOfficinaRepository,
    Ingresso,
)
from lys_workflow_hub.core.pratica_files import (
    UploadRifiutato,
    cartella_ingresso,
    save_ingresso_file,
)
from lys_workflow_hub.core.utenti_repository import Utente
from lys_workflow_hub.web.auth import get_current_user, template_context_processor, verify_csrf

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["operatore"])

# Stesso limite di `_MAX_FILES_PER_UPLOAD` in routes.py — condiviso solo per
# valore, non per import: quel modulo non deve dipendere da questo.
_MAX_FILES_PER_UPLOAD = 20


def get_ingressi_repo(settings: Settings = Depends(get_settings)) -> IngressiOfficinaRepository:
    return IngressiOfficinaRepository(db_path=settings.app_db_path)


def get_operatore_settings() -> Settings:
    return get_settings()


def _require_user(current_user: Utente | None) -> Utente:
    if current_user is None:
        raise HTTPException(401, "Autenticazione richiesta.")
    return current_user


def _richiedi_operatore(utente: Utente) -> None:
    if not utente.is_operatore:
        raise HTTPException(403, "Pagina riservata agli operatori d'officina.")


@router.get("/operatore", response_class=HTMLResponse)
def operatore_home(
    request: Request,
    current_user: Utente | None = Depends(get_current_user),
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
) -> HTMLResponse:
    utente = _require_user(current_user)
    _richiedi_operatore(utente)
    ingressi = repo.list_per_stato(STATO_IN_ATTESA)
    return templates.TemplateResponse(
        request,
        "operatore_home.html",
        {"version": __version__, "ingressi": ingressi},
    )


@router.post("/operatore/ingressi")
def operatore_crea_ingresso(
    request: Request,
    cliente_nominativo: str = Form(...),
    targa: str = Form(""),
    note: str = Form(""),
    csrf_token: str = Form(""),
    current_user: Utente | None = Depends(get_current_user),
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
) -> RedirectResponse:
    # Niente verify_csrf esplicito: body non-multipart, già verificato da
    # AuthMiddleware su ogni POST (vedi web/auth.py _is_multipart).
    utente = _require_user(current_user)
    _richiedi_operatore(utente)
    try:
        ingresso = repo.crea(
            cliente_nominativo=cliente_nominativo, targa=targa, note=note, creato_da=utente.id
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operatore/ingressi/{ingresso.id}", status_code=303)


def _get_ingresso_o_404(repo: IngressiOfficinaRepository, ingresso_id: int) -> Ingresso:
    ingresso = repo.get(ingresso_id)
    if ingresso is None:
        raise HTTPException(404, "Ingresso non trovato.")
    return ingresso


@router.get("/operatore/ingressi/{ingresso_id}", response_class=HTMLResponse)
def operatore_ingresso_detail(
    ingresso_id: int,
    request: Request,
    current_user: Utente | None = Depends(get_current_user),
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
) -> HTMLResponse:
    utente = _require_user(current_user)
    _richiedi_operatore(utente)
    ingresso = _get_ingresso_o_404(repo, ingresso_id)
    return templates.TemplateResponse(
        request,
        "operatore_ingresso_detail.html",
        {
            "version": __version__,
            "ingresso": ingresso,
            "tipi_file": TIPI_FILE,
            "tipo_file_labels": TIPO_FILE_LABELS,
        },
    )


@router.post("/operatore/ingressi/{ingresso_id}/upload")
def operatore_upload_file(
    ingresso_id: int,
    request: Request,
    tipo: str = Form(...),
    files: list[UploadFile] = File(...),
    csrf_token: str = Form(""),
    current_user: Utente | None = Depends(get_current_user),
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
    settings: Settings = Depends(get_operatore_settings),
) -> RedirectResponse:
    utente = _require_user(current_user)
    _richiedi_operatore(utente)
    if not verify_csrf(request, csrf_token):
        raise HTTPException(403, "Token di sicurezza mancante o scaduto. Ricarica la pagina e riprova.")
    ingresso = _get_ingresso_o_404(repo, ingresso_id)
    if not ingresso.is_in_attesa:
        raise HTTPException(400, "Questo ingresso è già stato collegato o annullato.")
    if tipo not in TIPI_FILE:
        raise HTTPException(400, "Tipo file non valido.")
    if len(files) > _MAX_FILES_PER_UPLOAD:
        raise HTTPException(400, f"Troppi file in un'unica richiesta (max {_MAX_FILES_PER_UPLOAD}).")

    categoria = "foto" if tipo == "foto_danno" else "documento"
    errori: list[str] = []
    for f in files:
        if not f.filename:
            continue
        try:
            raw = f.file.read()
            target = save_ingresso_file(
                archivio_root=settings.wincar_archivio,
                ingresso_id=ingresso_id,
                categoria=categoria,
                filename=f.filename,
                raw=raw,
            )
            repo.aggiungi_file(
                ingresso_id, tipo=tipo, nome_file=target.name, nome_file_originale=f.filename
            )
        except UploadRifiutato as exc:
            errori.append(f"{f.filename}: {exc}")
        except OSError as exc:
            logger.exception("Errore filesystem upload ingresso %s", ingresso_id)
            errori.append(f"{f.filename}: errore di filesystem ({exc})")

    esito = "&errori=" + str(len(errori)) if errori else ""
    return RedirectResponse(
        url=f"/operatore/ingressi/{ingresso_id}?caricati=1{esito}", status_code=303
    )


@router.post("/operatore/ingressi/{ingresso_id}/file/{file_id}/elimina")
def operatore_elimina_file(
    ingresso_id: int,
    file_id: int,
    request: Request,
    csrf_token: str = Form(""),
    current_user: Utente | None = Depends(get_current_user),
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
    settings: Settings = Depends(get_operatore_settings),
) -> RedirectResponse:
    # Niente verify_csrf esplicito: body non-multipart, già verificato da
    # AuthMiddleware su ogni POST.
    utente = _require_user(current_user)
    _richiedi_operatore(utente)
    ingresso = _get_ingresso_o_404(repo, ingresso_id)
    if not ingresso.is_in_attesa:
        raise HTTPException(400, "Questo ingresso è già stato collegato o annullato.")
    file_eliminato = repo.elimina_file(file_id, ingresso_id)
    if file_eliminato is not None:
        path = cartella_ingresso(settings.wincar_archivio, ingresso_id) / file_eliminato.nome_file
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Impossibile eliminare il file di staging %s: %s", path, exc)
    return RedirectResponse(url=f"/operatore/ingressi/{ingresso_id}", status_code=303)
