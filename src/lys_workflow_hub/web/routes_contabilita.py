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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.contabilita_categoria_repository import (
    ContabilitaCategoriaRepository,
    TIPI as CATEGORIA_TIPI,
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
from lys_workflow_hub.integrations.sdi import build_sdi_client
from lys_workflow_hub.workflows.contabilita.sdi_import import (
    importa_attive_da_dir,
    invia_attive_pendenti,
    sincronizza_passive,
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
        totali = mov_repo.totali(
            categoria_id=f_categoria,
            pratica_id=f_pratica,
            fattura_id=f_fattura,
            stato=f_stato,
            dal=f_dal,
            al=f_al,
        )
    except ValueError as exc:
        # Filtro data malformato: mostra lista vuota + errore, non 500.
        movimenti, totali = [], mov_repo.totali()
        filtro_err = str(exc)

    categorie = cat_repo.list_all()
    cat_by_id = {c.id: c for c in categorie}

    context = _ctx()
    context.update(
        movimenti=movimenti,
        totali=totali,
        categorie=categorie,
        cat_by_id=cat_by_id,
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
        return RedirectResponse(
            url=f"/contabilita/categorie?error={exc}", status_code=303
        )
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
        return RedirectResponse(
            url=f"/contabilita/categorie?error={exc}", status_code=303
        )
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
        return RedirectResponse(
            url="/contabilita/categorie?error=Categoria usata da movimenti esistenti: "
            "è stata disattivata invece di eliminata.",
            status_code=303,
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
    settings: Settings = Depends(get_contabilita_settings),
) -> HTMLResponse:
    f_tipo = tipo if tipo in (TIPO_ATTIVA, TIPO_PASSIVA) else None
    f_anno = _int_or_none(anno)
    fatture = fat_repo.list(tipo=f_tipo, anno=f_anno, limit=1000)
    pratiche_count = {f.id: len(fat_repo.list_pratiche(f.id)) for f in fatture}
    coda_passive = len(fat_repo.list_non_collegate(tipo=TIPO_PASSIVA))

    context = _ctx()
    context.update(
        fatture=fatture,
        pratiche_count=pratiche_count,
        coda_passive=coda_passive,
        filtri={"tipo": tipo or "", "anno": anno or ""},
        esito=esito,
        sdi_provider=settings.sdi_provider,
        sdi_test_mode=settings.sdi_test_mode,
        sdi_dir=str(settings.sdi_wincar_attive_dir),
    )
    return templates.TemplateResponse(request, "contabilita_fatture.html", context)


@router.post("/contabilita/fatture/importa-attive")
def fatture_importa_attive(
    fat_repo: ContabilitaFatturaRepository = Depends(get_fattura_repo),
    settings: Settings = Depends(get_contabilita_settings),
) -> RedirectResponse:
    s = importa_attive_da_dir(
        Path(settings.sdi_wincar_attive_dir),
        piva_azienda=settings.sdi_piva_azienda,
        fattura_repo=fat_repo,
        archivio_dir=Path(settings.app_archivio_fatture),
    )
    msg = f"Import attive: {s.nuove} nuove, {s.duplicate} già presenti, {len(s.errori)} errori."
    if s.errori:
        msg += " " + " · ".join(s.errori[:3])
    return RedirectResponse(url=f"/contabilita/fatture?esito={msg}", status_code=303)


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
    return RedirectResponse(url=f"/contabilita/fatture?esito={msg}", status_code=303)


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
    return RedirectResponse(url=f"/contabilita/fatture?esito={msg}", status_code=303)
