"""Portale utenti esterni (v3.0 fasi 3-4) — pratiche assegnate + collaborazione.

Nessuna `dependencies=[Depends(require_admin)]` qui: questo router è
l'eccezione "non admin-only" del progetto (vedi `web/auth.py`). Protetto
comunque da `AuthMiddleware` come tutto il resto (serve essere loggati),
ma qualunque utente (admin o esterno) può aprirlo — semplicemente vede solo
le pratiche assegnate al proprio account, che per un admin sono normalmente
zero (l'admin visualizza le pratiche da `/pratiche/{numero}`, non da qui).

Il dettaglio pratica (`/portale/pratiche/{numero}`) verifica sempre che la
pratica sia assegnata all'utente corrente (o che l'utente sia admin) prima
di mostrare qualunque dato — vedi `_verifica_accesso`.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.pratica_eventi_repository import PraticaEventiRepository
from lys_workflow_hub.core.pratica_files import scan as scan_allegati
from lys_workflow_hub.core.pratica_note_repository import PraticaNoteRepository
from lys_workflow_hub.core.utenti_repository import Utente
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.web.auth import get_current_user, template_context_processor
from lys_workflow_hub.web.routes import (
    _allegati_con_url,
    _NON_RENDERIZZABILI,
    _parse_date,
    resolve_pratica_file,
)


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["portale"])


def get_assegnazioni_repo(
    settings: Settings = Depends(get_settings),
) -> PraticaAssegnazioniRepository:
    return PraticaAssegnazioniRepository(db_path=settings.app_db_path)


def get_wincar_repo(settings: Settings = Depends(get_settings)) -> WinCarRepository:
    return WinCarRepository.from_settings(settings)


def get_portale_settings() -> Settings:
    """Wrapper dedicato (come `get_app_settings` in routes.py) così i test
    possono sovrascrivere solo le Settings di questo router — es. per puntare
    note/calendario e la scansione allegati su un DB/archivio temporaneo,
    senza toccare `get_settings` globale usato altrove."""
    return get_settings()


def _verifica_accesso(
    current_user: Utente, numero: int, assegnazioni_repo: PraticaAssegnazioniRepository
) -> None:
    """404 (non 403: non riveliamo l'esistenza della pratica) se l'utente
    esterno non è assegnatario. Gli admin passano sempre — utile per
    verificare cosa vede un collaboratore, anche se normalmente usano
    `/pratiche/{numero}`."""
    if current_user.is_admin:
        return
    numeri_assegnati = assegnazioni_repo.list_pratica_numeri_per_utente(current_user.id)
    if numero not in numeri_assegnati:
        raise HTTPException(404, "Pratica non trovata.")


@router.get("/portale", response_class=HTMLResponse)
def portale_list(
    request: Request,
    current_user: Utente | None = Depends(get_current_user),
    assegnazioni_repo: PraticaAssegnazioniRepository = Depends(get_assegnazioni_repo),
    wincar_repo: WinCarRepository = Depends(get_wincar_repo),
) -> HTMLResponse:
    numeri = (
        assegnazioni_repo.list_pratica_numeri_per_utente(current_user.id)
        if current_user
        else []
    )
    pratiche = []
    for numero in numeri:
        try:
            pratica = wincar_repo.get_pratica(numero)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Portale: impossibile leggere pratica %s da WinCar: %s", numero, exc)
            pratica = None
        if pratica is not None:
            pratiche.append(pratica)

    context = {"version": __version__, "pratiche": pratiche}
    return templates.TemplateResponse(request, "portale_list.html", context)


def _require_user(current_user: Utente | None) -> Utente:
    if current_user is None:
        raise HTTPException(401, "Autenticazione richiesta.")
    return current_user


@router.get("/portale/pratiche/{numero}", response_class=HTMLResponse)
def portale_pratica_detail(
    numero: int,
    request: Request,
    current_user: Utente | None = Depends(get_current_user),
    assegnazioni_repo: PraticaAssegnazioniRepository = Depends(get_assegnazioni_repo),
    wincar_repo: WinCarRepository = Depends(get_wincar_repo),
    settings: Settings = Depends(get_portale_settings),
) -> HTMLResponse:
    utente = _require_user(current_user)
    _verifica_accesso(utente, numero, assegnazioni_repo)

    pratica = wincar_repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, "Pratica non trovata.")

    context = {"version": __version__, "pratica": pratica, "numero": numero}

    try:
        allegati = scan_allegati(settings.wincar_archivio, numero)
        foto_renderizzabili = [
            a for a in allegati.foto if a.path.suffix.lower() not in _NON_RENDERIZZABILI
        ]
        foto_non_renderizzabili = [
            a for a in allegati.foto if a.path.suffix.lower() in _NON_RENDERIZZABILI
        ]
        context["foto_pratica"] = _allegati_con_url(
            numero, foto_renderizzabili, base="/portale/pratiche"
        )
        context["documenti_pratica"] = _allegati_con_url(
            numero,
            allegati.cessioni + allegati.denunce + allegati.altri + foto_non_renderizzabili,
            base="/portale/pratiche",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Portale: impossibile leggere foto/documenti per %s: %s", numero, exc)
        context["foto_pratica"] = []
        context["documenti_pratica"] = []

    note_repo = PraticaNoteRepository(db_path=settings.app_db_path)
    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    context["note_pratica"] = note_repo.list_per_pratica(numero)
    context["eventi_pratica"] = eventi_repo.list_per_pratica(numero)

    return templates.TemplateResponse(request, "portale_pratica_detail.html", context)


@router.get("/portale/pratiche/{numero}/file")
def portale_pratica_file_preview(
    numero: int,
    path: str = Query(..., description="Path assoluto del file (deve appartenere alla pratica)"),
    current_user: Utente | None = Depends(get_current_user),
    assegnazioni_repo: PraticaAssegnazioniRepository = Depends(get_assegnazioni_repo),
    settings: Settings = Depends(get_portale_settings),
) -> FileResponse:
    utente = _require_user(current_user)
    _verifica_accesso(utente, numero, assegnazioni_repo)
    return resolve_pratica_file(numero, path, settings)


@router.post("/portale/pratiche/{numero}/note")
def portale_aggiungi_nota(
    numero: int,
    testo: str = Form(...),
    current_user: Utente | None = Depends(get_current_user),
    assegnazioni_repo: PraticaAssegnazioniRepository = Depends(get_assegnazioni_repo),
    settings: Settings = Depends(get_portale_settings),
) -> RedirectResponse:
    utente = _require_user(current_user)
    _verifica_accesso(utente, numero, assegnazioni_repo)
    repo = PraticaNoteRepository(db_path=settings.app_db_path)
    try:
        repo.add(numero, utente.id, utente.nome or utente.email, testo)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/portale/pratiche/{numero}#note", status_code=303)


@router.post("/portale/pratiche/{numero}/eventi")
def portale_aggiungi_evento(
    numero: int,
    titolo: str = Form(...),
    data_evento: str = Form(...),
    current_user: Utente | None = Depends(get_current_user),
    assegnazioni_repo: PraticaAssegnazioniRepository = Depends(get_assegnazioni_repo),
    settings: Settings = Depends(get_portale_settings),
) -> RedirectResponse:
    utente = _require_user(current_user)
    _verifica_accesso(utente, numero, assegnazioni_repo)
    data = _parse_date(data_evento)
    if data is None:
        raise HTTPException(400, "Data evento non valida.")
    repo = PraticaEventiRepository(db_path=settings.app_db_path)
    try:
        repo.add(numero, titolo, data, utente.id, utente.nome or utente.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/portale/pratiche/{numero}#calendario", status_code=303)


@router.post("/portale/pratiche/{numero}/eventi/{evento_id}/elimina")
def portale_elimina_evento(
    numero: int,
    evento_id: int,
    current_user: Utente | None = Depends(get_current_user),
    assegnazioni_repo: PraticaAssegnazioniRepository = Depends(get_assegnazioni_repo),
    settings: Settings = Depends(get_portale_settings),
) -> RedirectResponse:
    utente = _require_user(current_user)
    _verifica_accesso(utente, numero, assegnazioni_repo)
    repo = PraticaEventiRepository(db_path=settings.app_db_path)
    repo.delete(evento_id, numero)
    return RedirectResponse(url=f"/portale/pratiche/{numero}#calendario", status_code=303)
