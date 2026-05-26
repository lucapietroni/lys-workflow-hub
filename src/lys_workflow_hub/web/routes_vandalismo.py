"""Pagine HTML del Workflow B — Richiesta risarcimento per atti vandalici (M2).

Route esposte:

    GET  /pratiche/{numero}/vandalismo               Anteprima editabile
    POST /pratiche/{numero}/vandalismo               Rigenera l'anteprima con override
    POST /pratiche/{numero}/vandalismo/scarica       Scarica la bozza come file .txt

L'invio effettivo via SMTP **non** fa parte di questa fase: la schermata di
anteprima produce subject + body pronti da copiare nel client PEC e l'elenco
degli allegati da agganciare manualmente.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.compagnie_repository import (
    Compagnia,
    CompagnieRepository,
)
from lys_workflow_hub.core.pec_log_repository import PecLogRepository
from lys_workflow_hub.core.wincar_repository import Pratica, WinCarRepository
from lys_workflow_hub.workflows.risarcimento_vandalismo import (
    AUTORITA_DENUNCIA,
    RichiestaVandalismoData,
    build_all,
    from_pratica,
    scan,
    selezione_nomi_default,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.allegati import (
    AllegatiPratica,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.invio_pec import (
    ParametriInvio,
    invia,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.data import (
    CARROZZERIA_NOME as VAND_CARROZZERIA_NOME,
)


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["vandalismo"])


# --------------------------------------------------------------------------- #
#  Dependency wiring
# --------------------------------------------------------------------------- #


def get_wincar_repo() -> WinCarRepository:
    return WinCarRepository.from_settings()


def get_compagnie_repo(
    settings: Settings = Depends(get_settings),
) -> CompagnieRepository:
    return CompagnieRepository(db_path=settings.app_db_path)


def get_pec_log_repo(
    settings: Settings = Depends(get_settings),
) -> PecLogRepository:
    return PecLogRepository(db_path=settings.app_db_path)


def _common_context() -> dict:
    return {"version": __version__}


# --------------------------------------------------------------------------- #
#  Form parsing
# --------------------------------------------------------------------------- #


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


_DATE_FIELDS = ("evento_data", "denuncia_data", "assicurato_data_nascita")


def _build_overrides(form: dict[str, Any]) -> dict[str, Any]:
    """Converte la FormData HTML in override tipizzati per `from_pratica`."""
    overrides: dict[str, Any] = {}
    for key, raw in form.items():
        if key in _DATE_FIELDS:
            overrides[key] = _parse_date(raw)
        elif key == "e_ditta":
            overrides[key] = str(raw).lower() in ("on", "true", "1", "yes")
        elif key == "assicurato_sesso":
            overrides[key] = "F" if str(raw).upper() == "F" else "M"
        elif key == "allegati_selezionati":
            # campo multivalore, gestito separatamente sotto
            continue
        else:
            overrides[key] = raw.strip() if isinstance(raw, str) else raw
    overrides.setdefault("e_ditta", False)
    return overrides


def _allegati_selezionati(form_multi) -> list[str]:
    """Estrae i nomi file delle checkbox selezionate."""
    try:
        values = form_multi.getlist("allegati_selezionati")
    except AttributeError:
        values = []
    return [v for v in values if v]


# --------------------------------------------------------------------------- #
#  Carico pratica + dati di base
# --------------------------------------------------------------------------- #


def _carica_pratica(numero: int, repo: WinCarRepository) -> Pratica:
    pratica = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, f"Pratica n. {numero} non trovata in WinCar.")
    return pratica


def _trova_compagnia(
    nome: str | None, repo: CompagnieRepository
) -> Compagnia | None:
    if not nome or not nome.strip():
        return None
    return repo.lookup_by_name(nome)


def _build_data(
    pratica: Pratica,
    compagnia: Compagnia | None,
    settings: Settings,
    overrides: dict[str, Any] | None = None,
) -> RichiestaVandalismoData:
    return from_pratica(
        pratica,
        compagnia=compagnia,
        carrozzeria_pec=settings.carrozzeria_pec,
        carrozzeria_email=settings.carrozzeria_email,
        carrozzeria_telefono=settings.carrozzeria_telefono,
        carrozzeria_referente=settings.carrozzeria_referente,
        overrides=overrides,
    )


def _build_context(
    request: Request,
    pratica: Pratica,
    data: RichiestaVandalismoData,
    settings: Settings,
    selezione: list[str] | None,
    pec_log: PecLogRepository | None = None,
) -> dict:
    context = _common_context()
    context["pratica"] = pratica
    context["numero"] = pratica.numero
    context["data"] = data
    context["mancanti"] = data.campi_mancanti()
    context["autorita_options"] = AUTORITA_DENUNCIA

    # Se è già stata inviata una PEC per questa pratica, banner di avviso.
    if pec_log is not None:
        context["ultima_pec_ok"] = pec_log.last_ok_for_pratica(pratica.numero)
    else:
        context["ultima_pec_ok"] = None

    # Scansione allegati: sia foto che documenti.
    allegati = scan(settings.wincar_archivio, pratica.numero)
    context["allegati"] = allegati

    # Default checkbox = cessione recente + denunce + tutte le foto.
    # Se è il primo render, usiamo i default; se è un POST viene passata la
    # lista selezionata effettivamente dall'utente.
    if selezione is None:
        selezione_default = list(selezione_nomi_default(allegati))
        selezionati_set = set(selezione_default)
    else:
        selezionati_set = set(selezione)
    context["allegati_selezionati_set"] = selezionati_set

    # Bozza PEC con SOLO gli allegati effettivamente selezionati.
    selezionati_obj = [a for a in allegati.tutti if a.nome_file in selezionati_set]
    from lys_workflow_hub.workflows.risarcimento_vandalismo.allegati import (
        AllegatiPratica,
    )
    allegati_filtrati = AllegatiPratica(
        foto=[a for a in selezionati_obj if a.categoria == "foto"],
        denunce=[a for a in selezionati_obj if a.categoria == "denuncia"],
        cessioni=[a for a in selezionati_obj if a.categoria == "cessione"],
        altri=[a for a in selezionati_obj if a.categoria == "altro"],
    )
    bozza = build_all(data, allegati=allegati_filtrati)
    context["pec_subject"] = bozza["subject"]
    context["pec_body"] = bozza["body"]
    context["pec_filename"] = bozza["filename"]

    return context


# --------------------------------------------------------------------------- #
#  Route
# --------------------------------------------------------------------------- #


@router.get(
    "/pratiche/{numero}/vandalismo",
    response_class=HTMLResponse,
)
def vandalismo_preview(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_wincar_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    pec_log: PecLogRepository = Depends(get_pec_log_repo),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    pratica = _carica_pratica(numero, repo)
    compagnia = _trova_compagnia(
        pratica.assicurazione_cliente.nome, compagnie_repo
    )
    data = _build_data(pratica, compagnia, settings)
    context = _build_context(
        request, pratica, data, settings, selezione=None, pec_log=pec_log,
    )
    context["compagnia_match"] = compagnia
    return templates.TemplateResponse(request, "vandalismo_preview.html", context)


@router.post(
    "/pratiche/{numero}/vandalismo",
    response_class=HTMLResponse,
)
async def vandalismo_rigenera(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_wincar_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    pec_log: PecLogRepository = Depends(get_pec_log_repo),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Rigenera l'anteprima usando i valori dell'operatore."""
    pratica = _carica_pratica(numero, repo)
    form = await request.form()
    form_dict = {k: form.get(k) for k in form.keys() if k != "allegati_selezionati"}
    selezione = _allegati_selezionati(form)

    overrides = _build_overrides(form_dict)
    # Lookup della compagnia: se l'operatore ha modificato il nome a video,
    # usiamo il nuovo valore per il lookup; il record selezionato ha
    # comunque la precedenza nei campi PEC/indirizzo.
    nome_compagnia = (
        overrides.get("polizza_compagnia_nome") or pratica.assicurazione_cliente.nome
    )
    compagnia = _trova_compagnia(nome_compagnia, compagnie_repo)
    data = _build_data(pratica, compagnia, settings, overrides=overrides)

    context = _build_context(
        request, pratica, data, settings, selezione=selezione, pec_log=pec_log,
    )
    context["compagnia_match"] = compagnia
    return templates.TemplateResponse(request, "vandalismo_preview.html", context)


@router.post("/pratiche/{numero}/vandalismo/scarica")
async def vandalismo_scarica(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_wincar_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Scarica la bozza PEC come file di testo con tutti i metadati in testa."""
    pratica = _carica_pratica(numero, repo)
    form = await request.form()
    form_dict = {k: form.get(k) for k in form.keys() if k != "allegati_selezionati"}
    selezione = _allegati_selezionati(form)
    overrides = _build_overrides(form_dict)
    nome_compagnia = (
        overrides.get("polizza_compagnia_nome") or pratica.assicurazione_cliente.nome
    )
    compagnia = _trova_compagnia(nome_compagnia, compagnie_repo)
    data = _build_data(pratica, compagnia, settings, overrides=overrides)

    allegati = scan(settings.wincar_archivio, pratica.numero)
    selezionati_set = set(selezione)
    selezionati_obj = [a for a in allegati.tutti if a.nome_file in selezionati_set]
    from lys_workflow_hub.workflows.risarcimento_vandalismo.allegati import (
        AllegatiPratica,
    )
    allegati_filtrati = AllegatiPratica(
        foto=[a for a in selezionati_obj if a.categoria == "foto"],
        denunce=[a for a in selezionati_obj if a.categoria == "denuncia"],
        cessioni=[a for a in selezionati_obj if a.categoria == "cessione"],
        altri=[a for a in selezionati_obj if a.categoria == "altro"],
    )
    bozza = build_all(data, allegati=allegati_filtrati)

    # Compongo un .txt con header utili in cima (per ricordare allegati e destinatario).
    header_lines = [
        f"Destinatario PEC : {data.compagnia_pec or '(da inserire)'}",
        f"Oggetto          : {bozza['subject']}",
        "Allegati da agganciare al messaggio:",
    ]
    for f in selezionati_obj:
        header_lines.append(f"  - {f.path}")
    if not selezionati_obj:
        header_lines.append("  (nessun allegato selezionato)")
    header_lines.append("")
    header_lines.append("-" * 72)
    header_lines.append("")

    content = "\n".join(header_lines) + bozza["body"]

    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{bozza["filename"]}"',
        },
    )


# --------------------------------------------------------------------------- #
#  Servizio file: serve gli allegati della pratica per anteprima nelle pagine
# --------------------------------------------------------------------------- #


@router.get("/pratiche/{numero}/allegato")
def vandalismo_serve_allegato(
    numero: int,
    nome: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Serve un singolo file allegato della pratica (foto o documento).

    Importante: questa route NON apre il DB WinCar. Il browser carica le
    miniature delle foto in parallelo e il driver Access ODBC (Jet) non
    sopporta connessioni concorrenti sullo stesso .mdb, quindi una verifica
    DB qui causerebbe errori 500 sporadici durante il rendering della pagina.
    L'esistenza della pratica viene verificata controllando che esista la
    cartella sul filesystem.

    Sicurezza: il parametro `nome` viene confrontato con i risultati di
    `scan()` della pratica; vengono serviti solo i file effettivamente
    elencati dallo scanner. Nessun path traversal possibile.
    """
    # Sanity check basico sul nome.
    if not nome or "/" in nome or "\\" in nome or ".." in nome:
        raise HTTPException(400, "Nome file non valido.")

    # Esistenza pratica via filesystem (no DB, vedi docstring).
    cartella_pratica = settings.wincar_archivio / "Pratiche" / str(numero)
    if not cartella_pratica.is_dir():
        raise HTTPException(404, f"Pratica n. {numero} non trovata.")

    allegati = scan(settings.wincar_archivio, numero)
    match = next((a for a in allegati.tutti if a.nome_file == nome), None)
    if match is None:
        raise HTTPException(
            404, f"Allegato {nome!r} non trovato per pratica {numero}."
        )

    # Doppio controllo: il path risolto deve restare dentro la cartella della pratica.
    base = cartella_pratica.resolve()
    try:
        resolved = match.path.resolve()
        resolved.relative_to(base)
    except (OSError, ValueError) as exc:
        raise HTTPException(403, "Percorso non consentito.") from exc

    # PDF e immagini: inline (il browser apre nella tab, non scarica).
    # Per i tipi inline NON passare filename= a FileResponse: Starlette
    # chiamerebbe setdefault("content-disposition", "attachment; ...") sul
    # dict Python (case-sensitive) producendo due header duplicati e Chrome
    # userebbe "attachment". Usiamo chiave lowercase e niente filename=.
    # Per altri tipi: attachment con filename= (comportamento default).
    _INLINE_EXTS = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".tif": "image/tiff", ".tiff": "image/tiff",
        ".txt": "text/plain; charset=utf-8",
    }
    ext = resolved.suffix.lower()
    if ext in _INLINE_EXTS:
        return FileResponse(
            path=resolved,
            media_type=_INLINE_EXTS[ext],
            headers={"content-disposition": f'inline; filename="{nome}"'},
        )
    return FileResponse(path=resolved, filename=nome)


# --------------------------------------------------------------------------- #
#  Invio PEC (M2-bis): conferma + esecuzione + esito
# --------------------------------------------------------------------------- #


def _ricostruisci_invio(
    numero: int,
    form_dict: dict[str, Any],
    selezione: list[str],
    repo: WinCarRepository,
    compagnie_repo: CompagnieRepository,
    settings: Settings,
) -> tuple[Pratica, RichiestaVandalismoData, Compagnia | None, AllegatiPratica, list]:
    """Ricostruisce dati + compagnia + allegati selezionati dai parametri form.

    Usato sia dalla pagina di conferma (GET con query string) sia
    dall'esecuzione effettiva (POST). Restituisce anche la lista
    `Allegato` filtrata in base alla selezione checkbox.
    """
    pratica = _carica_pratica(numero, repo)
    overrides = _build_overrides(form_dict)
    nome_compagnia = (
        overrides.get("polizza_compagnia_nome") or pratica.assicurazione_cliente.nome
    )
    compagnia = _trova_compagnia(nome_compagnia, compagnie_repo)
    data = _build_data(pratica, compagnia, settings, overrides=overrides)
    allegati = scan(settings.wincar_archivio, pratica.numero)
    nomi = set(selezione)
    filtrati = [a for a in allegati.tutti if a.nome_file in nomi]
    return pratica, data, compagnia, allegati, filtrati


def _build_parametri_invio(
    numero_pratica: int,
    data: RichiestaVandalismoData,
    compagnia: Compagnia | None,
    allegati_selezionati: list,
    settings: Settings,
) -> ParametriInvio:
    """Compone l'oggetto ParametriInvio a partire da dati + Settings."""
    bozza = build_all(
        data,
        allegati=AllegatiPratica(
            foto=[a for a in allegati_selezionati if a.categoria == "foto"],
            denunce=[a for a in allegati_selezionati if a.categoria == "denuncia"],
            cessioni=[a for a in allegati_selezionati if a.categoria == "cessione"],
            altri=[a for a in allegati_selezionati if a.categoria == "altro"],
        ),
    )
    sender_email = settings.pec_smtp_user or settings.carrozzeria_pec
    sender_display = (
        settings.carrozzeria_pec_alias or VAND_CARROZZERIA_NOME
    )
    return ParametriInvio(
        numero_pratica=numero_pratica,
        compagnia_id=(compagnia.id if compagnia else None),
        compagnia_nome=data.polizza_compagnia_nome or (compagnia.nome if compagnia else ""),
        sender_email=sender_email,
        sender_display=sender_display,
        # NIENTE Reply-To di default: vogliamo che la compagnia risponda alla
        # nostra stessa PEC (il `From:`), così la risposta ha valore legale e
        # finisce nella casella che il polling M3 monitora. Mettere qui la
        # mail ordinaria farebbe sì che "Rispondi" del client PEC compili la
        # casella sbagliata.
        reply_to="",
        recipient_email=data.compagnia_pec,
        subject=bozza["subject"],
        body=bozza["body"],
        allegati=allegati_selezionati,
        smtp_host=settings.pec_smtp_host,
        smtp_port=int(settings.pec_smtp_port),
        smtp_user=settings.pec_smtp_user,
        smtp_password=settings.pec_smtp_password,
        dry_run=bool(settings.pec_dry_run),
        archivio_pec_root=settings.app_archivio_pec,
    )


@router.post("/pratiche/{numero}/vandalismo/conferma", response_class=HTMLResponse)
async def vandalismo_conferma(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_wincar_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Pagina di conferma prima dell'invio reale.

    Riceve il form dalla pagina di anteprima (stessi campi + checkbox allegati).
    Mostra: destinatario, oggetto, body completo, allegati con dimensione
    totale, eventuali warning (dry-run attivo, campi mancanti).
    """
    form = await request.form()
    form_dict = {k: form.get(k) for k in form.keys() if k != "allegati_selezionati"}
    selezione = _allegati_selezionati(form)

    pratica, data, compagnia, _allegati_all, selezionati = _ricostruisci_invio(
        numero, form_dict, selezione, repo, compagnie_repo, settings
    )

    params = _build_parametri_invio(numero, data, compagnia, selezionati, settings)
    dim_totale = params.stima_dimensione_bytes()
    dim_label = (
        f"{dim_totale / 1024 / 1024:.1f} MB"
        if dim_totale >= 1024 * 1024
        else f"{dim_totale / 1024:.0f} KB"
    )

    context = _common_context()
    context["pratica"] = pratica
    context["numero"] = numero
    context["data"] = data
    context["compagnia"] = compagnia
    context["pec_subject"] = params.subject
    context["pec_body"] = params.body
    context["allegati_selezionati"] = selezionati
    context["dimensione_totale_bytes"] = dim_totale
    context["dimensione_totale_label"] = dim_label
    context["dry_run"] = bool(settings.pec_dry_run)
    context["mancanti"] = data.campi_mancanti()
    context["sender_email"] = params.sender_email
    context["sender_display"] = params.sender_display
    # I valori del form vanno preservati per il POST di invio
    context["form_values"] = form_dict
    context["allegati_selezionati_nomi"] = [a.nome_file for a in selezionati]
    return templates.TemplateResponse(request, "vandalismo_conferma.html", context)


@router.post("/pratiche/{numero}/vandalismo/invia", response_class=HTMLResponse)
async def vandalismo_invia(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_wincar_repo),
    compagnie_repo: CompagnieRepository = Depends(get_compagnie_repo),
    pec_log: PecLogRepository = Depends(get_pec_log_repo),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Esegue l'invio (o dry-run) e mostra la pagina di esito."""
    form = await request.form()
    form_dict = {k: form.get(k) for k in form.keys() if k != "allegati_selezionati"}
    selezione = _allegati_selezionati(form)

    pratica, data, compagnia, _allegati_all, selezionati = _ricostruisci_invio(
        numero, form_dict, selezione, repo, compagnie_repo, settings
    )

    # Blocco di sicurezza: se mancano campi obbligatori, torniamo alla pagina di conferma con alert.
    mancanti = data.campi_mancanti()
    if mancanti:
        raise HTTPException(
            400,
            "Impossibile inviare: ci sono ancora campi obbligatori vuoti: "
            + ", ".join(mancanti),
        )

    params = _build_parametri_invio(numero, data, compagnia, selezionati, settings)
    esito = invia(params, repo=pec_log)

    context = _common_context()
    context["pratica"] = pratica
    context["numero"] = numero
    context["esito"] = esito
    context["record"] = esito.record
    context["dry_run"] = esito.dry_run
    return templates.TemplateResponse(request, "vandalismo_esito.html", context)
