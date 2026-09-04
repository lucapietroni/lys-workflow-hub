"""Pagine HTML per la contabilità gestionale interna (Fase 1).

Riservate agli admin. NON contabilità fiscale: solo tracciamento analitico di
ricavi/costi per leggere il margine per pratica e la spesa per categoria.

Route esposte:
    GET  /contabilita                          Redirect alla lista movimenti
    GET  /contabilita/movimenti                Lista filtrabile + totali
    GET  /contabilita/movimenti/nuovo          Form inserimento manuale
    POST /contabilita/movimenti/nuovo          Crea movimento
    GET  /contabilita/movimenti/{id}/modifica  Form di modifica
    POST /contabilita/movimenti/{id}/modifica  Aggiorna movimento
    POST /contabilita/movimenti/{id}/elimina   Elimina movimento
    GET  /contabilita/categorie                Lista categorie + form inline
    POST /contabilita/categorie/nuova          Crea categoria
    POST /contabilita/categorie/{id}/modifica  Aggiorna categoria (nome/tipo/attiva)
    POST /contabilita/categorie/{id}/elimina   Elimina (o disattiva se già usata)
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.contabilita_categoria_repository import (
    CATEGORIA_NOTA_CREDITO,
    ContabilitaCategoriaRepository,
    TIPI as CATEGORIA_TIPI,
)
from lys_workflow_hub.core.contabilita_costo_ricorrente_repository import (
    CADENZE,
    ContabilitaCostoRicorrenteRepository,
)
from lys_workflow_hub.core.contabilita_fattura_repository import (
    TIPO_ATTIVA,
    TIPO_PASSIVA,
    ContabilitaFatturaRepository,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ORIGINE_MANUALE,
    STATI as MOVIMENTO_STATI,
    STATO_CONFERMATO,
    TIPI as MOVIMENTO_TIPI,
    TIPO_ENTRATA,
    TIPO_USCITA,
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.core.wincar_fatture_repository import WinCarFattureRepository
from lys_workflow_hub.integrations.sdi import build_sdi_client
from lys_workflow_hub.workflows.contabilita.report import costruisci_report
from lys_workflow_hub.workflows.contabilita.ricorrenti import genera_movimenti_ricorrenti
from lys_workflow_hub.workflows.contabilita.sdi_import import (
    collega_attive_da_wincar,
    importa_attive_da_dir,
    invia_attive_pendenti,
    marca_da_inviare,
    sincronizza_passive,
)
from lys_workflow_hub.workflows.contabilita.smistamento import (
    Assegnazione,
    SmistamentoError,
    coda_da_smistare,
    smista_fattura,
)
from lys_workflow_hub.web.auth import require_admin, template_context_processor


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["contabilita"], dependencies=[Depends(require_admin)])


# --------------------------------------------------------------------------- #
#  Dependency (separata da get_settings così i test possono sostituirla)
# --------------------------------------------------------------------------- #


def get_contabilita_settings(settings: Settings = Depends(get_settings)) -> Settings:
    return settings


def get_categoria_repo(
    settings: Settings = Depends(get_contabilita_settings),
) -> ContabilitaCategoriaRepository:
    return ContabilitaCategoriaRepository(db_path=settings.app_db_path)


def get_movimento_repo(
    settings: Settings = Depends(get_contabilita_settings),
) -> ContabilitaMovimentoRepository:
    return ContabilitaMovimentoRepository(db_path=settings.app_db_path)


def get_fattura_repo(
    settings: Settings = Depends(get_contabilita_settings),
) -> ContabilitaFatturaRepository:
    return ContabilitaFatturaRepository(db_path=settings.app_db_path)


def get_ricorrente_repo(
    settings: Settings = Depends(get_contabilita_settings),
) -> ContabilitaCostoRicorrenteRepository:
    return ContabilitaCostoRicorrenteRepository(db_path=settings.app_db_path)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _ctx() -> dict:
    return {
        "version": __version__,
        "movimento_tipi": MOVIMENTO_TIPI,
        "movimento_stati": MOVIMENTO_STATI,
        "categoria_tipi": CATEGORIA_TIPI,
        "TIPO_ENTRATA": TIPO_ENTRATA,
        "TIPO_USCITA": TIPO_USCITA,
    }


def _int_or_none(value: str | None) -> int | None:
    s = (value or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _str_or_none(value: str | None) -> str | None:
    s = (value or "").strip()
    return s or None


def _form_str(form, key: str) -> str:
    v = form.get(key)
    return v.strip() if isinstance(v, str) else ""


def _redir(path: str, **params: str) -> RedirectResponse:
    """RedirectResponse con query string correttamente url-encoded."""
    qs = urlencode({k: v for k, v in params.items() if v})
    return RedirectResponse(url=f"{path}?{qs}" if qs else path, status_code=303)


# --------------------------------------------------------------------------- #
#  Movimenti — lista
# --------------------------------------------------------------------------- #


@router.get("/contabilita", response_class=HTMLResponse)
def contabilita_home() -> RedirectResponse:
    return RedirectResponse(url="/contabilita/movimenti", status_code=303)


@router.get("/contabilita/movimenti", response_class=HTMLResponse)
def movimenti_list(
    request: Request,
    categoria_id: str | None = None,
    pratica_id: str | None = None,
    fattura: str | None = None,
    tipo: str | None = None,
    stato: str | None = None,
    dal: str | None = None,
    al: str | None = None,
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
) -> HTMLResponse:
    f_categoria = _int_or_none(categoria_id)
    f_pratica = _int_or_none(pratica_id)
    f_fattura = _int_or_none(fattura)
    f_tipo = tipo if tipo in MOVIMENTO_TIPI else None
    f_stato = stato if stato in MOVIMENTO_STATI else None
    f_dal = _str_or_none(dal)
    f_al = _str_or_none(al)

    filtro_err: str | None = None
    proposti_n = 0
    totale_righe = 0
    try:
        movimenti = mov_repo.list(
            categoria_id=f_categoria,
            pratica_id=f_pratica,
            fattura_id=f_fattura,
            tipo=f_tipo,
            stato=f_stato,
            dal=f_dal,
            al=f_al,
        )
        totale_righe = mov_repo.conta(
            categoria_id=f_categoria, pratica_id=f_pratica, fattura_id=f_fattura,
            tipo=f_tipo, stato=f_stato, dal=f_dal, al=f_al,
        )
        # I totali in testata contano solo i movimenti confermati (i 'proposto'
        # da fatture SDI non sono ancora dato reale), a meno che non si stia
        # filtrando esplicitamente per stato.
        totali = mov_repo.totali(
            categoria_id=f_categoria,
            pratica_id=f_pratica,
            fattura_id=f_fattura,
            stato=f_stato or STATO_CONFERMATO,
            dal=f_dal,
            al=f_al,
        )
        if f_stato is None:
            proposti_n = len(
                mov_repo.list(
                    categoria_id=f_categoria, pratica_id=f_pratica,
                    fattura_id=f_fattura, stato="proposto",
                    dal=f_dal, al=f_al, limit=10000,
                )
            )
    except ValueError as exc:
        # Filtro data malformato: mostra lista vuota + errore, non 500.
        movimenti, totali = [], mov_repo.totali(stato=STATO_CONFERMATO)
        filtro_err = str(exc)

    categorie = cat_repo.list_all()
    cat_by_id = {c.id: c for c in categorie}

    context = _ctx()
    context.update(
        movimenti=movimenti,
        totali=totali,
        categorie=categorie,
        cat_by_id=cat_by_id,
        proposti_n=proposti_n,
        totale_righe=totale_righe,
        filtri={
            "categoria_id": categoria_id or "",
            "pratica_id": pratica_id or "",
            "tipo": tipo or "",
            "stato": stato or "",
            "dal": dal or "",
            "al": al or "",
        },
        filtro_err=filtro_err,
    )
    return templates.TemplateResponse(request, "contabilita_movimenti.html", context)


# --------------------------------------------------------------------------- #
#  Movimenti — crea / modifica
# --------------------------------------------------------------------------- #


def _render_movimento_form(
    request: Request,
    *,
    cat_repo: ContabilitaCategoriaRepository,
    movimento=None,
    error: str | None = None,
    values: dict | None = None,
) -> HTMLResponse:
    context = _ctx()
    if values is not None:
        form_values = values
    elif movimento is not None:
        form_values = {
            "data": movimento.data.isoformat(),
            "importo": f"{movimento.importo:.2f}",
            "tipo": movimento.tipo,
            "categoria_id": str(movimento.categoria_id or ""),
            "pratica_id": str(movimento.pratica_id or ""),
            "descrizione": movimento.descrizione,
            "importo_iva": (
                f"{movimento.importo_iva:.2f}" if movimento.importo_iva is not None else ""
            ),
            "stato": movimento.stato,
        }
    else:
        from datetime import date as _date

        form_values = {
            "data": _date.today().isoformat(),
            "importo": "",
            "tipo": TIPO_USCITA,
            "categoria_id": "",
            "pratica_id": "",
            "descrizione": "",
            "importo_iva": "",
            "stato": STATO_CONFERMATO,
        }
    context.update(
        movimento=movimento,
        error=error,
        values=form_values,
        categorie=cat_repo.list_all(solo_attive=True),
    )
    return templates.TemplateResponse(request, "contabilita_movimento_form.html", context)


@router.get("/contabilita/movimenti/nuovo", response_class=HTMLResponse)
def movimento_new_form(
    request: Request,
    pratica_id: str | None = None,
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
) -> HTMLResponse:
    values = None
    if _int_or_none(pratica_id) is not None:
        from datetime import date as _date

        values = {
            "data": _date.today().isoformat(),
            "importo": "",
            "tipo": TIPO_USCITA,
            "categoria_id": "",
            "pratica_id": str(_int_or_none(pratica_id)),
            "descrizione": "",
            "importo_iva": "",
            "stato": STATO_CONFERMATO,
        }
    return _render_movimento_form(request, cat_repo=cat_repo, values=values)


@router.post("/contabilita/movimenti/nuovo")
async def movimento_new_submit(
    request: Request,
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
):
    form = await request.form()
    values = {
        "data": _form_str(form, "data"),
        "importo": _form_str(form, "importo"),
        "tipo": _form_str(form, "tipo"),
        "categoria_id": _form_str(form, "categoria_id"),
        "pratica_id": _form_str(form, "pratica_id"),
        "descrizione": _form_str(form, "descrizione"),
        "importo_iva": _form_str(form, "importo_iva"),
        "stato": _form_str(form, "stato") or STATO_CONFERMATO,
    }
    try:
        mov_repo.create(
            data=values["data"],
            importo=values["importo"],
            tipo=values["tipo"],
            categoria_id=_int_or_none(values["categoria_id"]),
            pratica_id=_int_or_none(values["pratica_id"]),
            descrizione=values["descrizione"],
            origine=ORIGINE_MANUALE,
            stato=values["stato"] if values["stato"] in MOVIMENTO_STATI else STATO_CONFERMATO,
            importo_iva=values["importo_iva"] or None,
        )
    except ValueError as exc:
        return _render_movimento_form(
            request, cat_repo=cat_repo, error=str(exc), values=values
        )
    return RedirectResponse(url="/contabilita/movimenti", status_code=303)


@router.get("/contabilita/movimenti/{movimento_id}/modifica", response_class=HTMLResponse)
def movimento_edit_form(
    movimento_id: int,
    request: Request,
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
) -> HTMLResponse:
    movimento = mov_repo.get(movimento_id)
    if movimento is None:
        raise HTTPException(404, f"Movimento id={movimento_id} non trovato.")
    return _render_movimento_form(request, cat_repo=cat_repo, movimento=movimento)


@router.post("/contabilita/movimenti/{movimento_id}/modifica")
async def movimento_edit_submit(
    movimento_id: int,
    request: Request,
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
):
    movimento = mov_repo.get(movimento_id)
    if movimento is None:
        raise HTTPException(404, f"Movimento id={movimento_id} non trovato.")
    form = await request.form()
    values = {
        "data": _form_str(form, "data"),
        "importo": _form_str(form, "importo"),
        "tipo": _form_str(form, "tipo"),
        "categoria_id": _form_str(form, "categoria_id"),
        "pratica_id": _form_str(form, "pratica_id"),
        "descrizione": _form_str(form, "descrizione"),
        "importo_iva": _form_str(form, "importo_iva"),
        "stato": _form_str(form, "stato") or movimento.stato,
    }
    try:
        mov_repo.update(
            movimento_id,
            data=values["data"],
            importo=values["importo"],
            tipo=values["tipo"],
            categoria_id=_int_or_none(values["categoria_id"]),
            pratica_id=_int_or_none(values["pratica_id"]),
            descrizione=values["descrizione"],
            importo_iva=values["importo_iva"] or None,
            stato=values["stato"] if values["stato"] in MOVIMENTO_STATI else None,
        )
    except ValueError as exc:
        return _render_movimento_form(
            request, cat_repo=cat_repo, movimento=movimento, error=str(exc), values=values
        )
    return RedirectResponse(url="/contabilita/movimenti", status_code=303)


@router.post("/contabilita/movimenti/{movimento_id}/elimina")
def movimento_delete(
    movimento_id: int,
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
) -> RedirectResponse:
    if not mov_repo.delete(movimento_id):
        raise HTTPException(404, f"Movimento id={movimento_id} non trovato.")
    return RedirectResponse(url="/contabilita/movimenti", status_code=303)


# --------------------------------------------------------------------------- #
#  Categorie
# --------------------------------------------------------------------------- #


@router.get("/contabilita/categorie", response_class=HTMLResponse)
def categorie_list(
    request: Request,
    error: str | None = None,
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
) -> HTMLResponse:
    context = _ctx()
    context.update(categorie=cat_repo.list_all(), error=error)
    return templates.TemplateResponse(request, "contabilita_categorie.html", context)


@router.post("/contabilita/categorie/nuova")
async def categoria_new(
    request: Request,
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
):
    form = await request.form()
    try:
        cat_repo.create(nome=_form_str(form, "nome"), tipo=_form_str(form, "tipo"))
    except ValueError as exc:
        return _redir("/contabilita/categorie", error=str(exc))
    return RedirectResponse(url="/contabilita/categorie", status_code=303)


@router.post("/contabilita/categorie/{categoria_id}/modifica")
async def categoria_edit(
    categoria_id: int,
    request: Request,
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
):
    form = await request.form()
    try:
        cat_repo.update(
            categoria_id,
            nome=_form_str(form, "nome"),
            tipo=_form_str(form, "tipo"),
            attiva=_form_str(form, "attiva") == "1",
        )
    except ValueError as exc:
        return _redir("/contabilita/categorie", error=str(exc))
    return RedirectResponse(url="/contabilita/categorie", status_code=303)


@router.post("/contabilita/categorie/{categoria_id}/elimina")
def categoria_delete(
    categoria_id: int,
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
) -> RedirectResponse:
    if cat_repo.get(categoria_id) is None:
        raise HTTPException(404, f"Categoria id={categoria_id} non trovata.")
    if not cat_repo.delete(categoria_id):
        # Referenziata da movimenti: disattiva invece di cancellare.
        cat_repo.set_attiva(categoria_id, False)
        return _redir(
            "/contabilita/categorie",
            error="Categoria usata da movimenti esistenti: è stata disattivata invece di eliminata.",
        )
    return RedirectResponse(url="/contabilita/categorie", status_code=303)


# --------------------------------------------------------------------------- #
#  Fatture SDI (Fase 3)
# --------------------------------------------------------------------------- #


@router.get("/contabilita/fatture", response_class=HTMLResponse)
def fatture_list(
    request: Request,
    tipo: str | None = None,
    anno: str | None = None,
    esito: str | None = None,
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
    settings: Settings = Depends(get_contabilita_settings),
) -> HTMLResponse:
    f_tipo = tipo if tipo in (TIPO_ATTIVA, TIPO_PASSIVA) else None
    f_anno = _int_or_none(anno)
    fatture = fat_repo.list(tipo=f_tipo, anno=f_anno, limit=1000)
    pratiche_count = {f.id: len(fat_repo.list_pratiche(f.id)) for f in fatture}
    da_smistare = len(mov_repo.fattura_ids_con_proposti())

    from datetime import date as _date

    context = _ctx()
    context.update(
        fatture=fatture,
        pratiche_count=pratiche_count,
        coda_da_smistare=da_smistare,
        filtri={"tipo": tipo or "", "anno": anno or ""},
        esito=esito,
        categorie=cat_repo.list_all(solo_attive=True),
        anno_default=_date.today().year,
        import_since=settings.sdi_attive_import_since,
        sdi_provider=settings.sdi_provider,
        sdi_test_mode=settings.sdi_test_mode,
        sdi_dir=str(settings.sdi_wincar_attive_dir),
    )
    return templates.TemplateResponse(request, "contabilita_fatture.html", context)


CATEGORIA_ATTIVE_DEFAULT = "Riparazioni carrozzeria"


def _categoria_id_per_nome(cat_repo: ContabilitaCategoriaRepository, nome: str) -> int | None:
    for c in cat_repo.list_all():
        if c.nome.strip().lower() == nome.strip().lower():
            return c.id
    return None


def _categoria_attive_id(cat_repo: ContabilitaCategoriaRepository) -> int | None:
    """id della categoria di default per le fatture attive WinCar."""
    return _categoria_id_per_nome(cat_repo, CATEGORIA_ATTIVE_DEFAULT)


def _categoria_nc_id(cat_repo: ContabilitaCategoriaRepository) -> int | None:
    return _categoria_id_per_nome(cat_repo, CATEGORIA_NOTA_CREDITO)


@router.post("/contabilita/fatture/importa-attive")
async def fatture_importa_attive(
    request: Request,
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
    settings: Settings = Depends(get_contabilita_settings),
) -> RedirectResponse:
    from datetime import date as _date

    form = await request.form()
    anno = _int_or_none(_form_str(form, "anno")) or _date.today().year
    # Le fatture attive di WinCar sono sempre lavori di carrozzeria: se il form
    # non forza una categoria diversa, usa "Riparazioni carrozzeria".
    categoria_id = _int_or_none(_form_str(form, "categoria_id")) or _categoria_attive_id(cat_repo)
    come_storico = _form_str(form, "come_storico") != "0"

    since = None
    if (settings.sdi_attive_import_since or "").strip():
        try:
            since = _date.fromisoformat(settings.sdi_attive_import_since.strip())
        except ValueError:
            since = None

    s = importa_attive_da_dir(
        Path(settings.sdi_wincar_attive_dir),
        piva_azienda=settings.sdi_piva_azienda,
        fattura_repo=fat_repo,
        movimento_repo=mov_repo,
        wincar_fatture_repo=WinCarFattureRepository.from_settings(settings),
        anno=anno,
        since=since,
        come_storico=come_storico,
        categoria_id=categoria_id,
        categoria_nc_id=_categoria_nc_id(cat_repo),
        archivio_dir=Path(settings.app_archivio_fatture),
    )
    stato_txt = "storico (non verranno inviate)" if come_storico else "da inviare"
    msg = (
        f"Import attive {anno} [{stato_txt}]: {s.nuove} nuove "
        f"({s.collegate_pratica} collegate a pratica), "
        f"{s.duplicate} già presenti, {s.fuori_periodo} fuori periodo, "
        f"{len(s.errori)} errori."
    )
    if s.errori:
        msg += " " + " · ".join(s.errori[:3])
    return _redir("/contabilita/fatture", esito=msg)


@router.post("/contabilita/fatture/attive/collega-pratiche")
def fatture_collega_pratiche(
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
    settings: Settings = Depends(get_contabilita_settings),
) -> RedirectResponse:
    """One-shot: collega alle pratiche le fatture attive già importate,
    leggendo il numero pratica da wcFatture.mdb."""
    wincar = WinCarFattureRepository.from_settings(settings)
    if not wincar.disponibile():
        return _redir(
            "/contabilita/fatture",
            esito=f"wcFatture.mdb non raggiungibile ({wincar.db_path}). "
            "Serve girare sul PC con WinCar.",
        )
    s = collega_attive_da_wincar(
        fattura_repo=fat_repo,
        movimento_repo=mov_repo,
        wincar_fatture_repo=wincar,
        categoria_id=_categoria_attive_id(cat_repo),
        categoria_nc_id=_categoria_nc_id(cat_repo),
    )
    msg = (
        f"Sistemate attive: {s.collegate} con pratica, "
        f"{s.categorizzate} solo categoria (pratica non in WinCar), "
        f"{s.gia_sistemate} già a posto, {len(s.errori)} errori."
    )
    if s.errori:
        msg += " " + " · ".join(s.errori[:3])
    return _redir("/contabilita/fatture", esito=msg)


@router.post("/contabilita/fatture/{fattura_id}/segna-da-inviare")
def fattura_segna_da_inviare(
    fattura_id: int,
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
) -> RedirectResponse:
    try:
        marca_da_inviare(fat_repo, fattura_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _redir(
        "/contabilita/fatture",
        esito="Fattura segnata 'da inviare'. Usa 'Invia attive allo SDI' per trasmetterla.",
    )


@router.post("/contabilita/fatture/invia-sdi")
def fatture_invia_sdi(
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    settings: Settings = Depends(get_contabilita_settings),
) -> RedirectResponse:
    client = build_sdi_client(settings)
    s = invia_attive_pendenti(
        client=client,
        fattura_repo=fat_repo,
        movimento_repo=mov_repo,
        disabilitato=bool(settings.sdi_invio_disabilitato),
    )
    msg = (
        f"Invio SDI ({settings.sdi_provider}"
        f"{', test' if settings.sdi_test_mode else ''}): "
        f"{s.inviate} inviate, {s.scartate} scartate, {s.movimenti_creati} movimenti proposti."
    )
    if settings.sdi_invio_disabilitato:
        msg = "Invio SDI disabilitato da configurazione (SDI_INVIO_DISABILITATO)."
    if s.errori:
        msg += " " + " · ".join(s.errori[:3])
    return _redir("/contabilita/fatture", esito=msg)


@router.post("/contabilita/fatture/sincronizza-passive")
def fatture_sincronizza_passive(
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    settings: Settings = Depends(get_contabilita_settings),
) -> RedirectResponse:
    from datetime import date as _date

    since = None
    if (settings.sdi_fetch_since or "").strip():
        try:
            since = _date.fromisoformat(settings.sdi_fetch_since.strip())
        except ValueError:
            since = None
    client = build_sdi_client(settings)
    s = sincronizza_passive(
        client=client,
        fattura_repo=fat_repo,
        movimento_repo=mov_repo,
        piva_azienda=settings.sdi_piva_azienda,
        since=since,
        archivio_dir=Path(settings.app_archivio_fatture),
    )
    msg = (
        f"Sync passive ({settings.sdi_provider}): {s.nuove} nuove, "
        f"{s.duplicate} già presenti, {s.movimenti_creati} movimenti proposti, "
        f"{len(s.errori)} errori."
    )
    return _redir("/contabilita/fatture", esito=msg)


# --------------------------------------------------------------------------- #
#  Coda smistamento fatture passive (Fase 4)
# --------------------------------------------------------------------------- #


@router.get("/contabilita/fatture/passive/da-collegare", response_class=HTMLResponse)
def coda_smistamento(
    request: Request,
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
) -> HTMLResponse:
    context = _ctx()
    context["coda"] = coda_da_smistare(fat_repo, mov_repo)
    return templates.TemplateResponse(request, "contabilita_smistamento_coda.html", context)


def _render_smista_form(
    request: Request,
    *,
    fattura,
    movimento,
    cat_repo: ContabilitaCategoriaRepository,
    error: str | None = None,
    righe: list[dict] | None = None,
) -> HTMLResponse:
    context = _ctx()
    context.update(
        fattura=fattura,
        movimento=movimento,
        categorie=cat_repo.list_all(solo_attive=True),
        error=error,
        righe=righe or [{"pratica_id": "", "importo": ""}],
        categoria_id=(movimento.categoria_id if movimento else None),
    )
    return templates.TemplateResponse(request, "contabilita_smistamento_form.html", context)


@router.get("/contabilita/fatture/{fattura_id}/smista", response_class=HTMLResponse)
def smista_form(
    fattura_id: int,
    request: Request,
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
) -> HTMLResponse:
    fattura = fat_repo.get(fattura_id)
    if fattura is None:
        raise HTTPException(404, f"Fattura id={fattura_id} non trovata.")
    proposti = [m for m in mov_repo.list_by_fattura(fattura_id) if m.stato == "proposto"]
    movimento = proposti[0] if proposti else None
    righe = [
        {"pratica_id": str(r.pratica_id), "importo": f"{r.importo_assegnato:.2f}"}
        for r in fat_repo.list_pratiche(fattura_id)
    ] or None
    return _render_smista_form(
        request, fattura=fattura, movimento=movimento, cat_repo=cat_repo, righe=righe
    )


@router.post("/contabilita/fatture/{fattura_id}/smista")
async def smista_submit(
    fattura_id: int,
    request: Request,
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
):
    fattura = fat_repo.get(fattura_id)
    if fattura is None:
        raise HTTPException(404, f"Fattura id={fattura_id} non trovata.")
    form = await request.form()
    categoria_id = _int_or_none(_form_str(form, "categoria_id"))
    try:
        praticas = form.getlist("pratica_id")
        importi = form.getlist("importo")
    except AttributeError:  # pragma: no cover
        praticas, importi = [], []

    righe_raw = [
        {"pratica_id": (p or "").strip(), "importo": (i or "").strip()}
        for p, i in zip(praticas, importi)
    ]
    assegnazioni: list[Assegnazione] = []
    for r in righe_raw:
        if not r["pratica_id"]:
            continue
        pid = _int_or_none(r["pratica_id"])
        try:
            imp = round(float((r["importo"] or "0").replace(",", ".")), 2)
        except ValueError:
            imp = -1.0
        if pid is None:
            continue
        assegnazioni.append(Assegnazione(pratica_id=pid, importo=imp))

    proposti = [m for m in mov_repo.list_by_fattura(fattura_id) if m.stato == "proposto"]
    movimento = proposti[0] if proposti else None
    try:
        smista_fattura(
            fattura_repo=fat_repo,
            movimento_repo=mov_repo,
            fattura_id=fattura_id,
            categoria_id=categoria_id,
            assegnazioni=assegnazioni,
        )
    except SmistamentoError as exc:
        return _render_smista_form(
            request, fattura=fattura, movimento=movimento, cat_repo=cat_repo,
            error=str(exc), righe=righe_raw or None,
        )
    return RedirectResponse(
        url="/contabilita/fatture/passive/da-collegare", status_code=303
    )


# --------------------------------------------------------------------------- #
#  Dashboard costi/ricavi (Fase 4)
# --------------------------------------------------------------------------- #


@router.get("/contabilita/report", response_class=HTMLResponse)
def report_dashboard(
    request: Request,
    dal: str | None = None,
    al: str | None = None,
    settings: Settings = Depends(get_contabilita_settings),
) -> HTMLResponse:
    f_dal = _str_or_none(dal)
    f_al = _str_or_none(al)
    error: str | None = None
    try:
        report = costruisci_report(settings.app_db_path, dal=f_dal, al=f_al)
    except ValueError as exc:
        error = str(exc)
        report = costruisci_report(settings.app_db_path)
    context = _ctx()
    context.update(report=report, filtri={"dal": dal or "", "al": al or ""}, error=error)
    return templates.TemplateResponse(request, "contabilita_report.html", context)


# --------------------------------------------------------------------------- #
#  Costi ricorrenti non fatturati (Fase 5)
# --------------------------------------------------------------------------- #


def _ricorrente_values(form) -> dict:
    return {
        "nome": _form_str(form, "nome"),
        "categoria_id": _form_str(form, "categoria_id"),
        "importo": _form_str(form, "importo"),
        "importo_iva": _form_str(form, "importo_iva"),
        "cadenza": _form_str(form, "cadenza"),
        "giorno_mese": _form_str(form, "giorno_mese") or "1",
        "data_inizio": _form_str(form, "data_inizio"),
        "descrizione": _form_str(form, "descrizione"),
        "attivo": _form_str(form, "attivo") == "1",
    }


@router.get("/contabilita/ricorrenti", response_class=HTMLResponse)
def ricorrenti_list(
    request: Request,
    error: str | None = None,
    esito: str | None = None,
    ric_repo: ContabilitaCostoRicorrenteRepository = Depends(get_ricorrente_repo),
    cat_repo: ContabilitaCategoriaRepository = Depends(get_categoria_repo),
) -> HTMLResponse:
    from datetime import date as _date

    context = _ctx()
    context.update(
        ricorrenti=ric_repo.list_all(),
        categorie=cat_repo.list_all(solo_attive=True),
        cat_by_id={c.id: c for c in cat_repo.list_all()},
        cadenze=CADENZE,
        oggi=_date.today().isoformat(),
        error=error,
        esito=esito,
    )
    return templates.TemplateResponse(request, "contabilita_ricorrenti.html", context)


@router.post("/contabilita/ricorrenti/nuovo")
async def ricorrente_new(
    request: Request,
    ric_repo: ContabilitaCostoRicorrenteRepository = Depends(get_ricorrente_repo),
):
    v = _ricorrente_values(await request.form())
    try:
        ric_repo.create(
            nome=v["nome"],
            categoria_id=_int_or_none(v["categoria_id"]),
            importo=v["importo"],
            importo_iva=v["importo_iva"] or None,
            cadenza=v["cadenza"],
            giorno_mese=v["giorno_mese"],
            data_inizio=v["data_inizio"],
            descrizione=v["descrizione"],
        )
    except ValueError as exc:
        return _redir("/contabilita/ricorrenti", error=str(exc))
    return _redir("/contabilita/ricorrenti")


def _prefisso_descr(costo) -> str:
    return f"{costo.descrizione or costo.nome} ("


@router.post("/contabilita/ricorrenti/{costo_id}/modifica")
async def ricorrente_edit(
    costo_id: int,
    request: Request,
    ric_repo: ContabilitaCostoRicorrenteRepository = Depends(get_ricorrente_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
):
    costo = ric_repo.get(costo_id)
    if costo is None:
        raise HTTPException(404, f"Costo ricorrente id={costo_id} non trovato.")
    v = _ricorrente_values(await request.form())
    try:
        aggiornato = ric_repo.update(
            costo_id,
            nome=v["nome"],
            categoria_id=_int_or_none(v["categoria_id"]),
            importo=v["importo"],
            importo_iva=v["importo_iva"] or None,
            cadenza=v["cadenza"],
            giorno_mese=v["giorno_mese"],
            data_inizio=v["data_inizio"],
            descrizione=v["descrizione"],
            attivo=v["attivo"],
        )
    except ValueError as exc:
        return _redir("/contabilita/ricorrenti", error=str(exc))
    # I movimenti già generati riflettono i vecchi valori: eliminali e
    # riazzera il contatore periodi. L'operatore riclicca "Genera" per
    # ricrearli aggiornati (oppure lo fa il ciclo giornaliero).
    n = mov_repo.delete_by_costo_ricorrente(
        costo_id, descrizione_prefix=_prefisso_descr(costo)
    )
    n += mov_repo.delete_by_costo_ricorrente(
        costo_id, descrizione_prefix=_prefisso_descr(aggiornato)
    )
    ric_repo.reset_watermark(costo_id)
    return _redir(
        "/contabilita/ricorrenti",
        esito=(
            f"Template aggiornato. {n} moviment"
            f"{'o generato eliminato' if n == 1 else 'i generati eliminati'}: "
            "riclicca «Genera movimenti scaduti adesso» per ricrearli aggiornati."
        ),
    )


@router.post("/contabilita/ricorrenti/{costo_id}/elimina")
def ricorrente_delete(
    costo_id: int,
    ric_repo: ContabilitaCostoRicorrenteRepository = Depends(get_ricorrente_repo),
    mov_repo: ContabilitaMovimentoRepository = Depends(get_movimento_repo),
) -> RedirectResponse:
    costo = ric_repo.get(costo_id)
    if costo is None:
        raise HTTPException(404, f"Costo ricorrente id={costo_id} non trovato.")
    n = mov_repo.delete_by_costo_ricorrente(
        costo_id, descrizione_prefix=_prefisso_descr(costo)
    )
    ric_repo.delete(costo_id)
    return _redir(
        "/contabilita/ricorrenti",
        esito=(
            f"Template e {n} moviment"
            f"{'o generato' if n == 1 else 'i generati'} eliminati."
        ),
    )


@router.post("/contabilita/ricorrenti/genera")
def ricorrenti_genera(
    settings: Settings = Depends(get_contabilita_settings),
) -> RedirectResponse:
    s = genera_movimenti_ricorrenti(settings.app_db_path)
    msg = (
        f"Generazione: {s.movimenti_creati} moviment"
        f"{'o' if s.movimenti_creati == 1 else 'i'} creat"
        f"{'o' if s.movimenti_creati == 1 else 'i'} da {s.template_esaminati} "
        f"templat{'e' if s.template_esaminati == 1 else 'i'}, {len(s.errori)} errori."
    )
    return _redir("/contabilita/ricorrenti", esito=msg)
