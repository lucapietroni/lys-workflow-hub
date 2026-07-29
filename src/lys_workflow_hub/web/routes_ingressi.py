"""Ingressi officina lato admin — coda dei veicoli in attesa di pratica
WinCar (creati dall'operatore, vedi `web/routes_operatore.py`).

"Collegare" un ingresso a un numero pratica (creato a mano in WinCar
dall'admin) rilegge ogni file di staging e lo salva con `save_upload()`
— la stessa funzione usata per l'upload normale — così ottiene
gratuitamente thumb WinCar e flag `CARVEI.F_FOTO` per le foto, poi
elimina la cartella di staging.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.ingressi_officina_repository import (
    STATO_IN_ATTESA,
    IngressiOfficinaRepository,
    Ingresso,
)
from lys_workflow_hub.core.pratica_files import cartella_ingresso, save_upload
from lys_workflow_hub.core.utenti_repository import Utente
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.web.auth import require_admin, template_context_processor
from lys_workflow_hub.web.routes import _PREVIEW_MIME

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["ingressi"], dependencies=[Depends(require_admin)])


def get_ingressi_repo(settings: Settings = Depends(get_settings)) -> IngressiOfficinaRepository:
    return IngressiOfficinaRepository(db_path=settings.app_db_path)


def get_wincar_repo() -> WinCarRepository:
    return WinCarRepository.from_settings()


def get_ingressi_settings() -> Settings:
    return get_settings()


def _get_ingresso_o_404(repo: IngressiOfficinaRepository, ingresso_id: int) -> Ingresso:
    ingresso = repo.get(ingresso_id)
    if ingresso is None:
        raise HTTPException(404, "Ingresso non trovato.")
    return ingresso


@router.get("/ingressi", response_class=HTMLResponse)
def ingressi_list(
    request: Request,
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
) -> HTMLResponse:
    ingressi = repo.list_per_stato(STATO_IN_ATTESA)
    return templates.TemplateResponse(
        request, "ingressi_list.html", {"version": __version__, "ingressi": ingressi}
    )


@router.get("/ingressi/{ingresso_id}", response_class=HTMLResponse)
def ingresso_detail(
    ingresso_id: int,
    request: Request,
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
) -> HTMLResponse:
    ingresso = _get_ingresso_o_404(repo, ingresso_id)
    return templates.TemplateResponse(
        request, "ingresso_detail.html", {"version": __version__, "ingresso": ingresso}
    )


@router.get("/ingressi/{ingresso_id}/file/{file_id}")
def ingresso_file_preview(
    ingresso_id: int,
    file_id: int,
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
    settings: Settings = Depends(get_ingressi_settings),
) -> FileResponse:
    """Serve un file di staging per l'anteprima admin. Sicurezza: il nome
    file servito viene sempre letto dalla riga DB (`file.nome_file`),
    trovata per `file_id` E `ingresso_id` insieme — mai costruito da input
    utente diretto, niente path traversal possibile."""
    ingresso = _get_ingresso_o_404(repo, ingresso_id)
    file = next((f for f in ingresso.file if f.id == file_id), None)
    if file is None:
        raise HTTPException(404, "File non trovato.")
    file_path = cartella_ingresso(settings.wincar_archivio, ingresso_id) / file.nome_file
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(410, "File non più disponibile sul filesystem.")
    ext = file_path.suffix.lower()
    if ext in _PREVIEW_MIME:
        return FileResponse(
            path=file_path,
            media_type=_PREVIEW_MIME[ext],
            headers={"content-disposition": f'inline; filename="{file_path.name}"'},
        )
    return FileResponse(path=file_path, filename=file.nome_file_originale)


@router.post("/ingressi/{ingresso_id}/collega")
def ingresso_collega(
    ingresso_id: int,
    request: Request,
    numero_pratica: int = Form(...),
    csrf_token: str = Form(""),
    admin: Utente = Depends(require_admin),
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
    wincar_repo: WinCarRepository = Depends(get_wincar_repo),
    settings: Settings = Depends(get_ingressi_settings),
) -> RedirectResponse:
    # Niente verify_csrf esplicito: body non-multipart, già verificato da
    # AuthMiddleware su ogni POST.
    ingresso = _get_ingresso_o_404(repo, ingresso_id)
    if not ingresso.is_in_attesa:
        raise HTTPException(400, "Questo ingresso è già stato collegato o annullato.")

    pratica = wincar_repo.get_pratica(numero_pratica)
    if pratica is None:
        raise HTTPException(
            400, f"Nessuna pratica WinCar con numero {numero_pratica}. Verifica il numero e riprova."
        )

    # Transizione di stato ATOMICA (WHERE stato='in_attesa') PRIMA di
    # toccare qualunque file: un doppio submit concorrente o un retry dopo
    # un errore a metà copia deve fallire subito qui con 409, non dopo aver
    # già ricopiato (duplicato) i file già spostati al tentativo precedente
    # — save_upload() non sovrascrive mai, genera sempre un nome nuovo.
    ok = repo.collega(ingresso_id, numero_pratica_wincar=numero_pratica, collegato_da=admin.id)
    if not ok:
        raise HTTPException(409, "Ingresso già collegato da un'altra richiesta concorrente.")

    staging_dir = cartella_ingresso(settings.wincar_archivio, ingresso_id)
    errori: list[str] = []
    for f in ingresso.file:
        sorgente = staging_dir / f.nome_file
        try:
            raw = sorgente.read_bytes()
        except OSError as exc:
            logger.error(
                "File di staging mancante per l'ingresso %s (%s): %s", ingresso_id, f.nome_file, exc
            )
            errori.append(f.nome_file_originale)
            continue
        try:
            save_upload(
                archivio_root=settings.wincar_archivio,
                numero_pratica=numero_pratica,
                categoria=f.categoria_upload,
                filename=f.nome_file_originale,
                raw=raw,
                odbc_driver=settings.wincar_odbc_driver,
            )
        except OSError as exc:
            logger.error(
                "Errore salvando %s (ingresso %s) sulla pratica %s: %s",
                f.nome_file_originale, ingresso_id, numero_pratica, exc,
            )
            errori.append(f.nome_file_originale)
            continue
        # Rimosso subito dopo il successo, non tutto insieme a fine loop: se
        # un file successivo fallisse, questo non deve essere ricopiato né
        # ricomparire in un'eventuale pulizia manuale — l'ingresso è già
        # 'collegato', non più ripetibile da qui.
        sorgente.unlink(missing_ok=True)

    if errori:
        # Stato già 'collegato' (transizione atomica sopra): niente retry
        # automatico possibile da qui. I file falliti restano nella cartella
        # di staging (non rimossa) per recupero manuale — vedi log sopra per
        # il dettaglio di ognuno.
        logger.warning(
            "Ingresso %s collegato alla pratica %s con %d file non spostati: %s",
            ingresso_id, numero_pratica, len(errori), ", ".join(errori),
        )
    else:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return RedirectResponse(url=f"/pratiche/{numero_pratica}", status_code=303)


@router.post("/ingressi/{ingresso_id}/annulla")
def ingresso_annulla(
    ingresso_id: int,
    request: Request,
    csrf_token: str = Form(""),
    repo: IngressiOfficinaRepository = Depends(get_ingressi_repo),
    settings: Settings = Depends(get_ingressi_settings),
) -> RedirectResponse:
    # Niente verify_csrf esplicito: body non-multipart, già verificato da
    # AuthMiddleware su ogni POST.
    ingresso = _get_ingresso_o_404(repo, ingresso_id)
    if repo.annulla(ingresso_id):
        staging_dir = cartella_ingresso(settings.wincar_archivio, ingresso_id)
        shutil.rmtree(staging_dir, ignore_errors=True)
    return RedirectResponse(url="/ingressi", status_code=303)
