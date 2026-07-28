"""Pagine HTML server-side rendered con Jinja2.

Route principali:
    GET  /                                  Home + form di ricerca
    GET  /pratiche/{numero}                 Dettaglio pratica (mostra anche scansioni firmate)
    GET  /pratiche/{numero}/cessione        Anteprima/edit dati cessione del credito
    POST /pratiche/{numero}/cessione        Genera e scarica il .docx
    POST /pratiche/{numero}/cessione/pdf    Genera e scarica il PDF
    POST /pratiche/{numero}/cessione/firmata Carica la scansione firmata e la archivia
"""
from __future__ import annotations

import calendar
import logging
import zipfile
from datetime import date, datetime, time
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote as _urlquote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.draft_repository import (
    DraftRepository,
    STATUS_PENDING,
    STATUS_READY,
)
from lys_workflow_hub.core.sollecito_repository import SollecitoRepository
from lys_workflow_hub.core.mail_in_repository import MailRepository
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.pratica_eventi_repository import PraticaEventiRepository
from lys_workflow_hub.core.pratica_files import Allegato, UploadRifiutato
from lys_workflow_hub.core.pratica_files import save_upload as save_pratica_upload
from lys_workflow_hub.core.pratica_files import scan as scan_allegati
from lys_workflow_hub.core.pratica_note_repository import PraticaNoteRepository
from lys_workflow_hub.core.pratica_stato_repository import (
    PraticaStatoRepository,
    STATI,
    STATO_LABELS,
    STATO_PERIZIATA,
)
from lys_workflow_hub.core.utenti_repository import Utente, UtentiRepository
from lys_workflow_hub.core.wincar_carvei_write import marca_foto_assente
from lys_workflow_hub.core.wincar_thumbs_index import rimuovi_frame
from lys_workflow_hub.core.wincar_repository import WinCarRepository
from lys_workflow_hub.integrations.notifier import (
    notify_esterno_nuova_attivita,
    notify_fcm_nuova_attivita,
    notify_push_nuova_attivita,
)
from lys_workflow_hub.workflows.cessione_credito import (
    PdfConversionError,
    docx_bytes_to_pdf_bytes,
    filename_for,
    from_pratica,
    generate,
    list_signed_pdfs,
    save_signed_pdf,
)
from lys_workflow_hub.workflows.verbale_cortesia.archive import list_verbali
from lys_workflow_hub.web.auth import require_admin, template_context_processor, verify_csrf


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[template_context_processor]
)

router = APIRouter(tags=["pages"], dependencies=[Depends(require_admin)])

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
PDF_MIME = "application/pdf"


def get_repository() -> WinCarRepository:
    return WinCarRepository.from_settings()


def get_app_settings() -> Settings:
    return get_settings()


def _common_context() -> dict:
    return {"version": __version__}


# Estensioni renderizzabili inline nel browser (foto pratica + documenti).
_PREVIEW_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
}

# Formati immagine che nessun browser renderizza in <img> (niente thumbnail
# in griglia foto pratica, vanno mostrati come documento scaricabile).
_NON_RENDERIZZABILI = {".heic", ".heif"}


def _allegati_con_url(
    numero: int, items: list[Allegato], base: str = "/pratiche"
) -> list[dict[str, Any]]:
    """Arricchisce gli Allegato con l'URL di anteprima inline per il template.

    `base` seleziona la route che servirà il file: `/pratiche` (admin-only,
    default) oppure `/portale/pratiche` (verifica assegnazione invece di
    require_admin) — vedi `routes_portale.py:portale_pratica_file_preview`.
    Un utente esterno con URL costruiti sul prefisso admin prenderebbe
    sempre 403 dal middleware require_admin, anche se autorizzato alla
    pratica.
    """
    return [
        {
            "nome_file": a.nome_file,
            "size_label": a.size_label,
            "data_modifica": a.data_modifica,
            "categoria": a.categoria,
            "path": str(a.path),
            "url": f"{base}/{numero}/file?path={_urlquote(str(a.path))}",
        }
        for a in items
    ]


# Numero massimo di voci nel feed "Attività recenti" — anche il numero di
# cambi stato da recuperare per costruirlo (vedi `pratica_detail` e
# `portale_pratica_detail`: usano uno storico stato dedicato con questo
# stesso limite, non quello tagliato a 5 per il widget "Storico ultimi cambi").
_FEED_LIMIT = 15


def _costruisci_feed_attivita(
    *,
    note: list,
    eventi: list,
    stato_storia: list,
    stato_labels: dict[str, str],
    foto: list[dict[str, Any]],
    documenti: list[dict[str, Any]],
    limit: int = _FEED_LIMIT,
) -> list[dict[str, Any]]:
    """Timeline unica di attività recenti su una pratica (note, eventi,
    cambi stato, upload foto/documenti), più recenti prima — usata sia da
    `/pratiche/{numero}` (admin) che da `/portale/pratiche/{numero}` per
    dare una visione d'insieme senza scorrere ogni singola sezione.

    I file (foto/documenti) hanno solo `data_modifica` (data, non orario:
    è il mtime del filesystem) e nessun autore tracciato — a differenza di
    note/eventi/stato che vivono nel DB con `created_at`/`changed_at` e
    autore. Per questi elementi il timestamp è la mezzanotte del giorno di
    modifica (`solo_data=True` dice al template di non mostrare un orario
    fittizio)."""
    voci: list[dict[str, Any]] = []
    for n in note:
        voci.append({
            "tipo": "nota",
            "icona": "📝",
            "timestamp": n.created_at,
            "solo_data": False,
            "label": f'{n.autore_nome} ha scritto una nota: "{n.testo}"',
        })
    for e in eventi:
        quando = f" per il {e.data_evento.strftime('%d/%m/%Y')}" if e.data_evento else ""
        voci.append({
            "tipo": "evento",
            "icona": "📅",
            "timestamp": e.created_at,
            "solo_data": False,
            "label": f'{e.creato_da_nome} ha aggiunto in calendario "{e.titolo}"{quando}',
        })
    for s in stato_storia:
        nota = f" — {s.note}" if s.note else ""
        voci.append({
            "tipo": "stato",
            "icona": "🔄",
            "timestamp": s.changed_at,
            "solo_data": False,
            "label": f'{s.changed_by} ha cambiato lo stato in '
                     f'"{stato_labels.get(s.stato, s.stato)}"{nota}',
        })
    for f in foto:
        voci.append({
            "tipo": "foto",
            "icona": "🖼️",
            "timestamp": datetime.combine(f["data_modifica"], time.min),
            "solo_data": True,
            "label": f'Nuova foto caricata: "{f["nome_file"]}"',
        })
    for d in documenti:
        voci.append({
            "tipo": "documento",
            "icona": "📎",
            "timestamp": datetime.combine(d["data_modifica"], time.min),
            "solo_data": True,
            "label": f'Nuovo documento caricato: "{d["nome_file"]}"',
        })
    voci = [v for v in voci if v["timestamp"] is not None]
    voci.sort(key=lambda v: v["timestamp"], reverse=True)
    return voci[:limit]


# --------------------------------------------------------------------------- #
#  Home & dettaglio pratica
# --------------------------------------------------------------------------- #


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str | None = None,
    repo: WinCarRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    """Home: form di ricerca; se `q` e' valorizzato mostra anche i risultati.

    Logica di ricerca: se `q` e' numerico cerca per numero pratica; se ha 7
    caratteri e contiene una cifra cerca per targa; altrimenti per cognome.
    """
    context = _common_context()
    context["query"] = q or ""
    context["results"] = []
    context["search_kind"] = None

    # KPI per la hero strip — silenzioso in caso di errore DB
    try:
        _draft_repo = DraftRepository(db_path=settings.app_db_path)
        _mail_repo = MailRepository(db_path=settings.app_db_path)
        _stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
        _sollecito_repo = SollecitoRepository(db_path=settings.app_db_path)
        _counts = _draft_repo.conta_per_status()
        context["kpi_bozze"] = (
            _counts.get(STATUS_PENDING, 0)
            + _counts.get(STATUS_READY, 0)
            + _sollecito_repo.conta_pending()
        )
        context["kpi_risposte_ar"] = _mail_repo.count_action_required()
        context["kpi_sla_breach"] = _stato_repo.count_sla_breach(
            sla_giorni=settings.sla_giorni_alert
        ) if settings.sla_giorni_alert > 0 else 0
    except Exception:
        context["kpi_bozze"] = 0
        context["kpi_risposte_ar"] = 0
        context["kpi_sla_breach"] = 0

    # Prossimi appuntamenti (v3.0 fase 5) — calendario condiviso, tutte le pratiche.
    # Esclude gli eventi delle pratiche già "periziata": l'appuntamento (es. perizia)
    # non è più rilevante una volta che la pratica ha superato quello stato.
    try:
        eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
        stato_repo_eventi = PraticaStatoRepository(db_path=settings.app_db_path)
        eventi_grezzi = []
        for e in eventi_repo.list_prossimi(entro_giorni=7):
            stato_obj = stato_repo_eventi.get_stato(e.pratica_numero)
            stato_corrente = stato_obj.stato if stato_obj else "aperta"
            if stato_corrente != STATO_PERIZIATA:
                eventi_grezzi.append(e)
        context["prossimi_eventi"] = _arricchisci_eventi_con_pratica(eventi_grezzi, repo)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere prossimi eventi: %s", exc)
        context["prossimi_eventi"] = []

    if q and q.strip():
        q_clean = q.strip()
        search_kind: str
        if q_clean.isdigit():
            search_kind = "numero"
            results = repo.search_pratiche(numero=int(q_clean), limit=20)
        elif len(q_clean) <= 8 and any(ch.isdigit() for ch in q_clean):
            search_kind = "targa"
            results = repo.search_pratiche(targa=q_clean, limit=20)
        else:
            search_kind = "cognome"
            results = repo.search_pratiche(cognome=q_clean, limit=20)
        context["results"] = results
        context["search_kind"] = search_kind
    else:
        # Nessuna ricerca in corso: mostra le ultime 20 pratiche aperte
        # invece dei suggerimenti statici — `search_pratiche()` senza filtri
        # ordina già per F_NUMPRA DESC (i numeri pratica WinCar sono
        # progressivi, quindi "più recenti" in pratica).
        try:
            context["ultime_pratiche"] = repo.search_pratiche(limit=20)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Impossibile leggere le ultime pratiche: %s", exc)
            context["ultime_pratiche"] = []

    return templates.TemplateResponse(request, "index.html", context)


@router.get("/calendario", response_class=HTMLResponse)
def calendario(
    request: Request,
    anno: int | None = None,
    mese: int | None = None,
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    """Vista mensile di tutti gli appuntamenti (tutte le pratiche, v3.0
    fase 5 parte F) — equivalente admin di `/portale/calendario`."""
    oggi = date.today()
    anno = anno or oggi.year
    mese = mese or oggi.month
    if not (1 <= mese <= 12):
        raise HTTPException(400, "Mese non valido.")

    context = _common_context()
    context.update(_contesto_calendario(anno, mese))

    try:
        eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
        eventi = eventi_repo.list_mese(anno, mese)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere eventi calendario %s-%s: %s", anno, mese, exc)
        eventi = []
    context["eventi_per_giorno"] = _raggruppa_per_giorno(eventi)
    context["pratica_link_base"] = "/pratiche"

    return templates.TemplateResponse(request, "calendario.html", context)


def _contesto_calendario(anno: int, mese: int) -> dict[str, Any]:
    """Dati di navigazione mese (griglia settimane, mese prec/succ) comuni
    a `/calendario` (admin) e `/portale/calendario` (esterno)."""
    primo_giorno_settimana = 0  # lunedì
    cal = calendar.Calendar(firstweekday=primo_giorno_settimana)
    settimane = cal.monthdatescalendar(anno, mese)

    mese_prec_anno, mese_prec = (anno - 1, 12) if mese == 1 else (anno, mese - 1)
    mese_succ_anno, mese_succ = (anno + 1, 1) if mese == 12 else (anno, mese + 1)

    return {
        "anno": anno,
        "mese": mese,
        "mese_label": _MESE_LABELS[mese - 1],
        "settimane": settimane,
        "oggi": date.today(),
        "mese_prec_anno": mese_prec_anno,
        "mese_prec": mese_prec,
        "mese_succ_anno": mese_succ_anno,
        "mese_succ": mese_succ,
    }


def _raggruppa_per_giorno(eventi: list) -> dict[date, list]:
    per_giorno: dict[date, list] = {}
    for e in eventi:
        if e.data_evento is None:
            continue
        per_giorno.setdefault(e.data_evento, []).append(e)
    return per_giorno


_MESE_LABELS = (
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
)


@router.get("/pratiche/{numero}", response_class=HTMLResponse)
def pratica_detail(
    numero: int,
    request: Request,
    uploaded: str | None = None,
    upload_ok: int = 0,
    errori: int = 0,
    repo: WinCarRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    pratica = repo.get_pratica(numero)
    context = _common_context()
    context["pratica"] = pratica
    context["numero"] = numero
    context["uploaded"] = uploaded
    context["upload_ok"] = upload_ok
    context["upload_errori"] = errori
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", context, status_code=404
        )
    # Lista delle scansioni gia' firmate per questa pratica
    try:
        context["scansioni"] = list_signed_pdfs(settings.wincar_archivio, numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere scansioni archiviate per %s: %s", numero, exc)
        context["scansioni"] = []
    # M3: risposte assicurative da gestire per questa pratica.
    try:
        mail_repo = MailRepository(db_path=settings.app_db_path)
        context["risposte_da_gestire"] = mail_repo.list_action_required_per_pratica(numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere risposte M3 per %s: %s", numero, exc)
        context["risposte_da_gestire"] = []
    # M5: stato pratica + SLA alert.
    try:
        stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
        context["pratica_stato"] = stato_repo.get_stato(numero)
        context["pratica_stato_storia"] = stato_repo.storia(numero, limit=5)
        context["stati_disponibili"] = STATI
        context["stato_labels"] = STATO_LABELS
        # SLA alert: questa pratica ha PEC senza risposta oltre soglia?
        if settings.sla_giorni_alert > 0:
            sla_alerts = stato_repo.lista_sla_alerts(
                sla_giorni=settings.sla_giorni_alert
            )
            context["sla_breach_questa"] = [
                a for a in sla_alerts if a.pratica_numero == numero
            ]
        else:
            context["sla_breach_questa"] = []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile caricare stato/SLA pratica %s: %s", numero, exc)
        context["pratica_stato"] = None
        context["pratica_stato_storia"] = []
        context["stati_disponibili"] = STATI
        context["stato_labels"] = STATO_LABELS
        context["sla_breach_questa"] = []
    # Solleciti SLA per questa pratica (per mostrare se già inviato)
    try:
        sol_repo = SollecitoRepository(db_path=settings.app_db_path)
        context["solleciti_questa"] = sol_repo.list_per_pratica(numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile caricare solleciti per %s: %s", numero, exc)
        context["solleciti_questa"] = []
    # Parametro URL per conferma cambio stato
    context["stato_aggiornato"] = bool(request.query_params.get("stato_aggiornato"))
    # Verbali cortesia già generati per questa pratica
    try:
        context["verbali_cortesia"] = list_verbali(settings.wincar_archivio, numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere verbali cortesia per %s: %s", numero, exc)
        context["verbali_cortesia"] = []
    # Foto e documenti archiviati nelle cartelle Pubblici/Foto e Pubblici/Allegati
    try:
        allegati = scan_allegati(settings.wincar_archivio, numero)
        # HEIC/HEIF (iPhone): nessun browser le renderizza in <img>, quindi non
        # vanno in griglia come miniatura (thumbnail rotta e silenziosa) — le
        # trattiamo come documento (link diretto, non anteprima inline).
        foto_renderizzabili = [
            a for a in allegati.foto if a.path.suffix.lower() not in _NON_RENDERIZZABILI
        ]
        foto_non_renderizzabili = [
            a for a in allegati.foto if a.path.suffix.lower() in _NON_RENDERIZZABILI
        ]
        context["foto_pratica"] = _allegati_con_url(numero, foto_renderizzabili)
        context["documenti_pratica"] = _allegati_con_url(
            numero,
            allegati.cessioni + allegati.denunce + allegati.altri + foto_non_renderizzabili,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere foto/documenti per %s: %s", numero, exc)
        context["foto_pratica"] = []
        context["documenti_pratica"] = []
    # Collaboratori esterni assegnati a questa pratica (v3.0 fase 3)
    try:
        assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
        # Singleton condiviso con AuthMiddleware/routes_utenti.py, non una
        # connessione nuova — vedi app.state.utenti_repo in main.py.
        utenti_repo: UtentiRepository = request.app.state.utenti_repo
        assegnati_ids = set(assegnazioni_repo.list_utente_ids_per_pratica(numero))
        tutti_utenti = utenti_repo.list_all()
        context["collaboratori_assegnati"] = [
            u for u in tutti_utenti if u.id in assegnati_ids
        ]
        context["esterni_disponibili"] = [
            u for u in tutti_utenti
            if u.ruolo == "esterno" and u.attivo and u.id not in assegnati_ids
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere assegnazioni per %s: %s", numero, exc)
        context["collaboratori_assegnati"] = []
        context["esterni_disponibili"] = []
    # Note e calendario condivisi con i collaboratori esterni (v3.0 fase 4)
    try:
        note_repo = PraticaNoteRepository(db_path=settings.app_db_path)
        eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
        context["note_pratica"] = note_repo.list_per_pratica(numero)
        context["eventi_pratica"] = eventi_repo.list_per_pratica(numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere note/calendario per %s: %s", numero, exc)
        context["note_pratica"] = []
        context["eventi_pratica"] = []
    # Feed attività unificato (note + eventi + cambi stato + upload). Usa uno
    # storico stato dedicato (non `pratica_stato_storia`, tagliato a 5 per il
    # widget "Storico ultimi cambi"): con >5 cambi di stato recenti i più
    # vecchi verrebbero scartati prima del merge, anche se rientrerebbero tra
    # i _FEED_LIMIT eventi più recenti del feed.
    try:
        stato_storia_feed = PraticaStatoRepository(
            db_path=settings.app_db_path
        ).storia(numero, limit=_FEED_LIMIT)
        context["feed_attivita"] = _costruisci_feed_attivita(
            note=context["note_pratica"],
            eventi=context["eventi_pratica"],
            stato_storia=stato_storia_feed,
            stato_labels=context["stato_labels"],
            foto=context["foto_pratica"],
            documenti=context["documenti_pratica"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile costruire il feed attività per %s: %s", numero, exc)
        context["feed_attivita"] = []
    return templates.TemplateResponse(request, "pratica_detail.html", context)


def _arricchisci_eventi_con_pratica(eventi: list, repo: WinCarRepository) -> list[dict]:
    """Aggiunge cliente/targa a ogni evento per il widget "Prossimi
    appuntamenti" (home admin e /portale), leggendo da WinCar. Tollera
    errori PER SINGOLO evento — un fallimento WinCar su una pratica non deve
    far sparire l'intero widget, solo quella riga resta senza cliente/targa.
    """
    arricchiti = []
    for e in eventi:
        cliente = ""
        targa = ""
        try:
            pratica = repo.get_pratica(e.pratica_numero)
            if pratica is not None:
                cliente = pratica.cliente.nominativo or ""
                targa = pratica.veicolo.targa or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Impossibile leggere cliente/targa per pratica %s: %s", e.pratica_numero, exc
            )
        arricchiti.append({"evento": e, "cliente": cliente, "targa": targa})
    return arricchiti


def _notifica_esterni_assegnati(
    request: Request,
    settings: Settings,
    numero: int,
    costruisci_messaggio: Callable[[], tuple[str, str]],
) -> None:
    """Email e/o push a ogni utente esterno assegnato alla pratica, secondo
    le preferenze self-service di ciascuno (v3.0 fase 5, parte D — vedi
    `UtentiRepository.set_notifiche` e `/portale/impostazioni`).

    `costruisci_messaggio` (subject, body) è chiamato QUI DENTRO, non dal
    chiamante: così anche un errore nella costruzione del testo (f-string,
    `settings.public_url`, `strftime`, ecc.) resta contenuto in questo
    try/except e non può far fallire la request — la nota/evento è già
    salvato quando questa funzione gira, la notifica è best-effort.
    """
    try:
        assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
        utenti_repo: UtentiRepository = request.app.state.utenti_repo
        assegnati_ids = set(assegnazioni_repo.list_utente_ids_per_pratica(numero))
        if not assegnati_ids:
            return
        subject, body_text = costruisci_messaggio()
        for u in utenti_repo.list_all():
            if u.id not in assegnati_ids or not u.attivo:
                continue
            if u.notify_email_enabled and u.email:
                notify_esterno_nuova_attivita(
                    smtp_host=settings.smtp_host,
                    smtp_port=settings.smtp_port,
                    smtp_user=settings.smtp_user,
                    smtp_password=settings.smtp_password,
                    smtp_sender=settings.smtp_from,
                    recipient=u.email,
                    subject=subject,
                    body_text=body_text,
                    smtp_tls=settings.smtp_tls,
                    disabled=settings.notify_disabled,
                )
            if u.notify_push_enabled and u.ntfy_topic:
                notify_push_nuova_attivita(
                    ntfy_server=settings.ntfy_server,
                    ntfy_topic=u.ntfy_topic,
                    titolo=subject,
                    messaggio=body_text,
                    disabled=settings.notify_disabled,
                )
            if u.fcm_token:
                notify_fcm_nuova_attivita(
                    fcm_project_id=settings.fcm_project_id,
                    fcm_credentials_path=str(settings.fcm_credentials_path or ""),
                    fcm_token=u.fcm_token,
                    titolo=subject,
                    messaggio=body_text,
                    click_path=f"/portale/pratiche/{numero}",
                    disabled=settings.notify_disabled,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile notificare esterni assegnati a %s: %s", numero, exc)


@router.post("/pratiche/{numero}/note")
def pratica_aggiungi_nota(
    numero: int,
    request: Request,
    testo: str = Form(...),
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    repo = PraticaNoteRepository(db_path=settings.app_db_path)
    try:
        repo.add(numero, admin.id, admin.nome or admin.email, testo)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _notifica_esterni_assegnati(
        request,
        settings,
        numero,
        costruisci_messaggio=lambda: (
            f"[LYS Hub] Nuova nota sulla pratica {numero}",
            f"{admin.nome or admin.email} ha scritto una nuova nota sulla pratica {numero}:\n\n"
            f"{testo}\n\n"
            f"Apri la pratica: {settings.public_url(f'/portale/pratiche/{numero}#note')}",
        ),
    )
    return RedirectResponse(url=f"/pratiche/{numero}#note", status_code=303)


@router.post("/pratiche/{numero}/eventi")
def pratica_aggiungi_evento(
    numero: int,
    request: Request,
    titolo: str = Form(...),
    data_evento: str = Form(...),
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    data = _parse_date(data_evento)
    if data is None:
        raise HTTPException(400, "Data evento non valida.")
    repo = PraticaEventiRepository(db_path=settings.app_db_path)
    try:
        repo.add(numero, titolo, data, admin.id, admin.nome or admin.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _notifica_esterni_assegnati(
        request,
        settings,
        numero,
        costruisci_messaggio=lambda: (
            f"[LYS Hub] Nuovo evento sulla pratica {numero}",
            f"{admin.nome or admin.email} ha aggiunto un evento sulla pratica {numero}:\n\n"
            f"{titolo} — {data.strftime('%d/%m/%Y')}\n\n"
            f"Apri la pratica: {settings.public_url(f'/portale/pratiche/{numero}#calendario')}",
        ),
    )
    return RedirectResponse(url=f"/pratiche/{numero}#calendario", status_code=303)


@router.post("/pratiche/{numero}/eventi/{evento_id}/elimina")
def pratica_elimina_evento(
    numero: int,
    evento_id: int,
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    repo = PraticaEventiRepository(db_path=settings.app_db_path)
    repo.delete(evento_id, numero)
    return RedirectResponse(url=f"/pratiche/{numero}#calendario", status_code=303)


@router.post("/pratiche/{numero}/note/{nota_id}/modifica")
def pratica_modifica_nota(
    numero: int,
    nota_id: int,
    testo: str = Form(...),
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    repo = PraticaNoteRepository(db_path=settings.app_db_path)
    try:
        repo.update(nota_id, numero, testo)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/pratiche/{numero}#note", status_code=303)


@router.post("/pratiche/{numero}/note/{nota_id}/elimina")
def pratica_elimina_nota(
    numero: int,
    nota_id: int,
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    repo = PraticaNoteRepository(db_path=settings.app_db_path)
    repo.delete(nota_id, numero)
    return RedirectResponse(url=f"/pratiche/{numero}#note", status_code=303)


def _notifica_esterno_assegnazione(
    request: Request, settings: Settings, numero: int, utente_id: int
) -> None:
    """Notifica il singolo utente appena assegnato a una pratica, secondo le
    sue preferenze self-service (v3.0 fase 6 — vedi `/portale/impostazioni`).

    A differenza di `_notifica_esterni_assegnati` (che notifica TUTTI gli
    assegnati correnti su nota/evento/stato), qui l'evento riguarda un solo
    utente: notificare anche gli altri già assegnati non avrebbe senso.
    """
    try:
        utenti_repo: UtentiRepository = request.app.state.utenti_repo
        u = utenti_repo.get(utente_id)
        if u is None or not u.attivo:
            return
        subject = f"[LYS Hub] Ti è stata assegnata la pratica {numero}"
        body_text = (
            f"Ti è stata assegnata la pratica {numero}.\n\n"
            f"Apri la pratica: {settings.public_url(f'/portale/pratiche/{numero}')}"
        )
        if u.notify_email_enabled and u.email:
            notify_esterno_nuova_attivita(
                smtp_host=settings.smtp_host,
                smtp_port=settings.smtp_port,
                smtp_user=settings.smtp_user,
                smtp_password=settings.smtp_password,
                smtp_sender=settings.smtp_from,
                recipient=u.email,
                subject=subject,
                body_text=body_text,
                smtp_tls=settings.smtp_tls,
                disabled=settings.notify_disabled,
            )
        if u.notify_push_enabled and u.ntfy_topic:
            notify_push_nuova_attivita(
                ntfy_server=settings.ntfy_server,
                ntfy_topic=u.ntfy_topic,
                titolo=subject,
                messaggio=body_text,
                disabled=settings.notify_disabled,
            )
        if u.fcm_token:
            notify_fcm_nuova_attivita(
                fcm_project_id=settings.fcm_project_id,
                fcm_credentials_path=str(settings.fcm_credentials_path or ""),
                fcm_token=u.fcm_token,
                titolo=subject,
                messaggio=body_text,
                click_path=f"/portale/pratiche/{numero}",
                disabled=settings.notify_disabled,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Impossibile notificare utente %s per assegnazione pratica %s: %s",
            utente_id, numero, exc,
        )


@router.post("/pratiche/{numero}/assegna")
def pratica_assegna(
    numero: int,
    request: Request,
    utente_id: int = Form(...),
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    nuova_assegnazione = repo.assegna(numero, utente_id, assegnato_da=admin.id)
    if nuova_assegnazione:
        _notifica_esterno_assegnazione(request, settings, numero, utente_id)
    return RedirectResponse(url=f"/pratiche/{numero}#collaboratori", status_code=303)


@router.post("/pratiche/{numero}/assegna/{utente_id}/rimuovi")
def pratica_rimuovi_assegnazione(
    numero: int,
    utente_id: int,
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
    repo.rimuovi(numero, utente_id)
    return RedirectResponse(url=f"/pratiche/{numero}#collaboratori", status_code=303)


def resolve_pratica_file(numero: int, path: str, settings: Settings) -> FileResponse:
    """Risolve e serve un file di Pubblici/Foto o Pubblici/Allegati.

    Sicurezza: il path richiesto deve corrispondere ESATTAMENTE a uno dei file
    trovati dalla scansione delle cartelle di questa pratica. Niente path
    traversal, niente file al di fuori delle cartelle WinCar della pratica.

    Estratta come funzione a sé (non solo la route `/pratiche/{numero}/file`)
    così `routes_portale.py` può riusarla con un controllo di accesso diverso
    (assegnazione invece di require_admin) senza duplicare la logica di
    validazione path — vedi `portale_pratica_file_preview`.
    """
    try:
        allegati = scan_allegati(settings.wincar_archivio, numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere allegati per %s: %s", numero, exc)
        raise HTTPException(404, "Pratica non trovata o cartelle non accessibili.")
    valid_paths = {str(a.path) for a in allegati.tutti}
    if path not in valid_paths:
        raise HTTPException(403, "File non autorizzato per questa pratica.")
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(410, f"File non piu' disponibile sul filesystem: {path}")
    ext = file_path.suffix.lower()
    # Solo i tipi che il browser sa renderizzare vanno inline (niente filename=,
    # header in minuscolo: evita che Starlette aggiunga un secondo
    # Content-Disposition: attachment, vedi bozza_allegato_preview). Gli altri
    # (.docx, .xlsx, .heic, ecc.) restano attachment col comportamento di default.
    if ext in _PREVIEW_MIME:
        return FileResponse(
            path=file_path,
            media_type=_PREVIEW_MIME[ext],
            headers={"content-disposition": f'inline; filename="{file_path.name}"'},
        )
    return FileResponse(path=file_path, filename=file_path.name)


@router.get("/pratiche/{numero}/file")
def pratica_file_preview(
    numero: int,
    path: str = Query(..., description="Path assoluto del file (deve appartenere alla pratica)"),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    return resolve_pratica_file(numero, path, settings)


def build_foto_zip(numero: int, paths: list[str], settings: Settings) -> Response:
    """Zippa le foto di una pratica: tutte se `paths` è vuoto, altrimenti solo
    quelle richieste (validate contro le foto reali della pratica — stesso
    principio di sicurezza di `resolve_pratica_file`, niente path arbitrari).

    Solo browser (no app): il download di un file generato via query string
    lunga non è gestito dalla WebView Capacitor, stesso limite già visto per
    i documenti prima del fix `@capacitor/browser` — qui non serve, la UI di
    selezione è nascosta in app via CSS (`html.is-app`).
    """
    try:
        allegati = scan_allegati(settings.wincar_archivio, numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere foto per %s: %s", numero, exc)
        raise HTTPException(404, "Pratica non trovata o cartelle non accessibili.")
    valid = {str(a.path): a for a in allegati.foto}
    selezionati = list(valid.values()) if not paths else [valid[p] for p in paths if p in valid]
    if not selezionati:
        raise HTTPException(400, "Nessuna foto valida selezionata.")

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        nomi_usati: set[str] = set()
        for a in selezionati:
            nome = a.nome_file
            if nome in nomi_usati:
                stem, suffix = Path(nome).stem, Path(nome).suffix
                i = 2
                while nome in nomi_usati:
                    nome = f"{stem}_{i}{suffix}"
                    i += 1
            nomi_usati.add(nome)
            zf.write(a.path, arcname=nome)

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="pratica_{numero}_foto.zip"'},
    )


@router.get("/pratiche/{numero}/foto/zip")
def pratica_foto_zip(
    numero: int,
    path: list[str] = Query(default=[]),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    return build_foto_zip(numero, path, settings)


# Un batch enorme (es. selezione multipla di centinaia di foto ad alta
# risoluzione) può arrivare a 20MB per file (vedi `save_upload`) — questo cap
# evita che una singola richiesta saturi memoria/tempo di risposta. Condiviso
# tra upload admin (qui sotto) e upload dal portale esterno (routes_portale.py).
_MAX_FILES_PER_UPLOAD = 20


def _salva_file_pratica(
    numero: int, categoria: str, files: list[UploadFile], settings: Settings
) -> tuple[list[str], list[str]]:
    """Salva ogni file valido in Pubblici/Foto o Pubblici/Allegati, continuando
    sugli altri se uno fallisce. Nessun controllo di accesso/CSRF qui dentro —
    responsabilità del chiamante (admin-only vs assegnazione esterno diversi
    tra routes.py e routes_portale.py, condividono solo questo nucleo)."""
    if len(files) > _MAX_FILES_PER_UPLOAD:
        raise HTTPException(400, f"Troppi file in un'unica richiesta (max {_MAX_FILES_PER_UPLOAD}).")

    salvati: list[str] = []
    errori: list[str] = []
    for f in files:
        if not f.filename:
            continue
        try:
            raw = f.file.read()
            save_pratica_upload(
                archivio_root=settings.wincar_archivio,
                numero_pratica=numero,
                categoria=categoria,
                filename=f.filename,
                raw=raw,
                odbc_driver=settings.wincar_odbc_driver,
            )
            salvati.append(f.filename)
        except UploadRifiutato as exc:
            errori.append(f"{f.filename}: {exc}")
        except OSError as exc:
            logger.exception("Errore filesystem upload %s pratica %s", categoria, numero)
            errori.append(f"{f.filename}: errore di filesystem ({exc})")
    return salvati, errori


def _upload_pratica_admin(
    numero: int,
    categoria: str,
    ancora: str,
    files: list[UploadFile],
    request: Request,
    csrf_token: str,
    admin: Utente,
    settings: Settings,
) -> RedirectResponse:
    """Upload admin di foto/documenti — simmetrico a `_upload_pratica` in
    routes_portale.py (stesso `_salva_file_pratica`), ma notifica gli esterni
    assegnati invece dell'admin (`_notifica_esterni_assegnati`, come
    nota/evento). CSRF esplicito: multipart è escluso dal middleware globale
    (vedi `web/auth.py`)."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(403, "Token di sicurezza mancante o scaduto. Ricarica la pagina e riprova.")

    salvati, errori = _salva_file_pratica(numero, categoria, files, settings)

    if salvati:
        _notifica_esterni_assegnati(
            request,
            settings,
            numero,
            costruisci_messaggio=lambda: (
                f"[LYS Hub] Nuovi file sulla pratica {numero}",
                f"{admin.nome or admin.email} ha caricato {len(salvati)} file "
                f"({categoria}) sulla pratica {numero}: {', '.join(salvati)}\n\n"
                f"Apri la pratica: {settings.public_url(f'/portale/pratiche/{numero}#{ancora}')}",
            ),
        )

    esito = "&errori=" + str(len(errori)) if errori else ""
    return RedirectResponse(
        url=f"/pratiche/{numero}?upload_ok={len(salvati)}{esito}#{ancora}",
        status_code=303,
    )


@router.post("/pratiche/{numero}/foto")
def pratica_upload_foto(
    numero: int,
    request: Request,
    files: list[UploadFile] = File(...),
    csrf_token: str = Form(""),
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    return _upload_pratica_admin(numero, "foto", "foto", files, request, csrf_token, admin, settings)


@router.post("/pratiche/{numero}/documenti")
def pratica_upload_documento(
    numero: int,
    request: Request,
    files: list[UploadFile] = File(...),
    csrf_token: str = Form(""),
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    return _upload_pratica_admin(
        numero, "documento", "documenti", files, request, csrf_token, admin, settings
    )


@router.post("/pratiche/{numero}/foto/elimina")
def pratica_elimina_foto(
    numero: int,
    path: str = Form(...),
    admin: Utente = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    """Elimina una foto (+ il suo sidecar .thumb) dalla pratica. Se era
    l'ultima rimasta, azzera anche CARVEI.F_FOTO: WinCar non lo fa da solo
    quando le foto vengono cancellate (bug segnalato dall'utente, icona
    fotocamera rimane accesa a cartella vuota) — vedi
    wincar_carvei_write.marca_foto_assente. Best-effort, come l'analogo
    marca_foto_presente sull'upload: non deve mai bloccare l'eliminazione
    vera e propria."""
    try:
        allegati = scan_allegati(settings.wincar_archivio, numero)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impossibile leggere foto per %s: %s", numero, exc)
        raise HTTPException(404, "Pratica non trovata o cartelle non accessibili.")
    valid = {str(a.path) for a in allegati.foto}
    if path not in valid:
        raise HTTPException(403, "Foto non autorizzata per questa pratica.")

    file_path = Path(path)
    nome_thumb = file_path.name + ".thumb"
    if not file_path.exists():
        # Non dovrebbe succedere: `path` è appena stato validato contro
        # scan_allegati() qui sopra, quindi il file c'era un istante fa.
        # Logghiamo comunque esplicitamente invece di lasciare che
        # unlink(missing_ok=True) mascheri silenziosamente la cosa — se il
        # problema segnalato ("non elimina il file fisico") è un mismatch di
        # path, questo è il punto in cui deve emergere nei log.
        logger.error("pratica_elimina_foto: file già assente su disco: %s", file_path)
    try:
        file_path.unlink(missing_ok=True)
        file_path.with_name(nome_thumb).unlink(missing_ok=True)
    except OSError as exc:
        logger.exception("pratica_elimina_foto: unlink fallito per %s", file_path)
        raise HTTPException(500, f"Impossibile eliminare il file: {exc}")
    if file_path.exists():
        logger.error(
            "pratica_elimina_foto: il file esiste ancora dopo unlink() senza errori: %s",
            file_path,
        )

    # Senza questo, WinCar continuerebbe a mostrare la miniatura di una foto
    # non più esistente (segnalato dall'utente) — l'indice condiviso non si
    # aggiorna da solo togliendo il file su disco. Best-effort come sopra.
    try:
        rimuovi_frame(file_path.parent / "Thumbs.thumb", nome_thumb=nome_thumb)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Impossibile rimuovere il frame da Thumbs.thumb per %s: %s", numero, exc
        )

    try:
        rimanenti = scan_allegati(settings.wincar_archivio, numero)
        if not rimanenti.foto:
            marca_foto_assente(
                archivio_root=settings.wincar_archivio,
                odbc_driver=settings.wincar_odbc_driver,
                numero_pratica=numero,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Impossibile aggiornare CARVEI.F_FOTO dopo eliminazione per %s: %s", numero, exc
        )

    return RedirectResponse(url=f"/pratiche/{numero}#foto", status_code=303)


# --------------------------------------------------------------------------- #
#  Workflow A — Cessione del credito
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


def _build_overrides(form: dict[str, Any]) -> dict[str, Any]:
    """Converte il dizionario form HTML in override tipizzati per `from_pratica`."""
    overrides: dict[str, Any] = {}
    for key, raw in form.items():
        if key in ("cedente_data_nascita", "sinistro_data"):
            overrides[key] = _parse_date(raw)
        elif key in ("e_ditta", "e_vandalismo"):
            overrides[key] = str(raw).lower() in ("on", "true", "1", "yes")
        elif key in ("cedente_sesso",):
            overrides[key] = "F" if str(raw).upper() == "F" else "M"
        else:
            overrides[key] = (raw or "").strip() if isinstance(raw, str) else raw
    overrides.setdefault("e_ditta", False)
    overrides.setdefault("e_vandalismo", False)
    return overrides


@router.get("/pratiche/{numero}/cessione", response_class=HTMLResponse)
def cessione_preview(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_repository),
) -> HTMLResponse:
    """Anteprima editabile della cessione del credito."""
    pratica = repo.get_pratica(numero)
    context = _common_context()
    context["numero"] = numero
    if pratica is None:
        return templates.TemplateResponse(
            request, "pratica_non_trovata.html", context, status_code=404
        )
    data = from_pratica(pratica)
    context["pratica"] = pratica
    context["data"] = data
    context["mancanti"] = data.campi_mancanti()
    return templates.TemplateResponse(request, "cessione_preview.html", context)


def _build_cessione_data(numero: int, form: dict[str, Any], repo: WinCarRepository):
    pratica = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, "Pratica non trovata.")
    overrides = _build_overrides(form)
    return pratica, from_pratica(pratica, overrides=overrides)


@router.post("/pratiche/{numero}/cessione")
async def cessione_generate_docx(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_repository),
) -> Response:
    """Genera e scarica il .docx di cessione del credito."""
    form = await request.form()
    _, data = _build_cessione_data(numero, dict(form), repo)
    docx_bytes = generate(data)
    fname = filename_for(data)
    return Response(
        content=docx_bytes,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/cessione/pdf")
async def cessione_generate_pdf(
    numero: int,
    request: Request,
    repo: WinCarRepository = Depends(get_repository),
) -> Response:
    """Genera e scarica il PDF di cessione del credito (per stampa)."""
    form = await request.form()
    _, data = _build_cessione_data(numero, dict(form), repo)
    docx_bytes = generate(data)
    try:
        pdf_bytes = docx_bytes_to_pdf_bytes(docx_bytes)
    except PdfConversionError as exc:
        # Errore esplicito al browser: l'utente vede il messaggio e capisce cosa fare.
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    fname = filename_for(data).replace(".docx", ".pdf")
    return Response(
        content=pdf_bytes,
        media_type=PDF_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/pratiche/{numero}/cessione/firmata")
async def cessione_upload_signed(
    numero: int,
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(""),
    repo: WinCarRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    """Carica la scansione firmata e la salva in Pratiche/<n>/Privati/.

    Verifica CSRF esplicita (non delegata al middleware): un body
    `multipart/form-data` letto da `AuthMiddleware` romperebbe l'upload —
    vedi il commento in `web/auth.py` su `_is_multipart`.
    """
    if not verify_csrf(request, csrf_token):
        raise HTTPException(403, "Token di sicurezza mancante o scaduto. Ricarica la pagina e riprova.")
    pratica = repo.get_pratica(numero)
    if pratica is None:
        raise HTTPException(404, "Pratica non trovata.")
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            400,
            f"Tipo file non supportato: {file.content_type}. Accettati solo PDF.",
        )
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(400, "File troppo grande (max 20 MB).")

    try:
        result = save_signed_pdf(
            archivio_root=settings.wincar_archivio,
            numero_pratica=numero,
            pdf_bytes=raw,
            central_archive_root=settings.app_archivio_cessioni or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        logger.exception("Errore di filesystem nel salvataggio scansione")
        raise HTTPException(500, f"Errore di filesystem: {exc}") from exc

    # Torniamo alla pagina dettaglio con un parametro che fa apparire il banner.
    return RedirectResponse(
        url=f"/pratiche/{numero}?uploaded={result.pratica_path.name}",
        status_code=303,
    )
