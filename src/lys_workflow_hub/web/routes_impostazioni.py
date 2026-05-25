"""Route per statistiche compagnie (M5.1) e impostazioni policy (M5.2).

Route:
    GET  /statistiche          Cruscotto statistiche per compagnia + AI
    GET  /impostazioni         Editor policy bozze per categoria
    POST /impostazioni         Salva policy bozze aggiornate
    GET  /pratiche/{numero}/stato   (redirect — stato aggiornato via POST)
    POST /pratiche/{numero}/stato   Cambia stato di una pratica
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lys_workflow_hub import __version__
from lys_workflow_hub.config import Settings, get_settings
from lys_workflow_hub.core.categoria_policy_repository import (
    POLICIES,
    POLICY_LABELS,
    CategoriaPolicyRepository,
)
from lys_workflow_hub.core.pratica_stato_repository import (
    STATI,
    STATO_LABELS,
    PraticaStatoRepository,
)


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["impostazioni"])


def get_settings_dep() -> Settings:
    return get_settings()


def _common_context() -> dict:
    return {"version": __version__}


# --------------------------------------------------------------------------- #
#  Helper: statistiche SQL (cross-tabella, stesso lys_hub.db)
# --------------------------------------------------------------------------- #


@dataclass
class StatCompagnia:
    """Riga aggregata per compagnia."""
    compagnia_nome: str
    pec_inviate: int
    risposte_ricevute: int
    avg_giorni_risposta: float | None
    n_presa_in_carico: int
    n_nomina_perito: int
    n_richiesta_documenti: int
    n_liquidazione: int
    n_altro: int
    ai_cost_tot: float


@dataclass
class StatGlobale:
    """KPI globali per la pagina statistiche."""
    pec_inviate_totali: int
    risposte_totali: int
    ai_cost_mese: float
    ai_cost_totale: float
    pratiche_con_stato: int


def _stats_compagnie(db_path: Path) -> tuple[list[StatCompagnia], StatGlobale]:
    """Statistiche aggregate per compagnia + globali."""

    @contextmanager
    def _conn() -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    try:
        with _conn() as conn:
            # Per compagnia
            rows = conn.execute("""
                SELECT
                    p.compagnia_nome,
                    COUNT(DISTINCT p.id)  AS pec_inviate,
                    COUNT(DISTINCT m.id)  AS risposte_ricevute,
                    ROUND(
                        AVG(
                            CASE WHEN m.classified_at IS NOT NULL
                            THEN julianday(m.classified_at) - julianday(p.data_invio)
                            END
                        ), 1
                    )                     AS avg_giorni_risposta,
                    SUM(CASE WHEN m.categoria='presa_in_carico'     THEN 1 ELSE 0 END) AS n_pic,
                    SUM(CASE WHEN m.categoria='nomina_perito'        THEN 1 ELSE 0 END) AS n_np,
                    SUM(CASE WHEN m.categoria='richiesta_documenti'  THEN 1 ELSE 0 END) AS n_rd,
                    SUM(CASE WHEN m.categoria='liquidazione'         THEN 1 ELSE 0 END) AS n_liq,
                    SUM(CASE WHEN m.categoria='altro'                THEN 1 ELSE 0 END) AS n_alt,
                    ROUND(COALESCE(SUM(m.ai_cost_eur), 0), 4)      AS ai_cost_tot
                FROM pec_inviate p
                LEFT JOIN mail_classificate m ON m.pec_inviata_id = p.id
                WHERE p.esito IN ('OK', 'DRY_RUN')
                GROUP BY p.compagnia_nome
                ORDER BY pec_inviate DESC, p.compagnia_nome
            """).fetchall()

            stats: list[StatCompagnia] = []
            for r in rows:
                stats.append(StatCompagnia(
                    compagnia_nome=r["compagnia_nome"] or "(sconosciuta)",
                    pec_inviate=int(r["pec_inviate"] or 0),
                    risposte_ricevute=int(r["risposte_ricevute"] or 0),
                    avg_giorni_risposta=(
                        float(r["avg_giorni_risposta"])
                        if r["avg_giorni_risposta"] is not None
                        else None
                    ),
                    n_presa_in_carico=int(r["n_pic"] or 0),
                    n_nomina_perito=int(r["n_np"] or 0),
                    n_richiesta_documenti=int(r["n_rd"] or 0),
                    n_liquidazione=int(r["n_liq"] or 0),
                    n_altro=int(r["n_alt"] or 0),
                    ai_cost_tot=float(r["ai_cost_tot"] or 0),
                ))

            # Globali
            mese_inizio = datetime.now().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat(timespec="seconds")

            g = conn.execute("""
                SELECT
                    COUNT(DISTINCT p.id)                             AS pec_tot,
                    COUNT(DISTINCT m.id)                             AS risp_tot,
                    ROUND(COALESCE(SUM(
                        CASE WHEN m.classified_at >= :mese
                        THEN m.ai_cost_eur ELSE 0 END
                    ), 0), 4)                                        AS cost_mese,
                    ROUND(COALESCE(SUM(m.ai_cost_eur), 0), 4)       AS cost_tot
                FROM pec_inviate p
                LEFT JOIN mail_classificate m ON m.pec_inviata_id = p.id
                WHERE p.esito IN ('OK', 'DRY_RUN')
            """, {"mese": mese_inizio}).fetchone()

            # Conta pratiche con almeno uno stato registrato
            n_stato = conn.execute(
                "SELECT COUNT(DISTINCT pratica_numero) AS n FROM pratica_stato"
            ).fetchone()

            globale = StatGlobale(
                pec_inviate_totali=int(g["pec_tot"] or 0),
                risposte_totali=int(g["risp_tot"] or 0),
                ai_cost_mese=float(g["cost_mese"] or 0),
                ai_cost_totale=float(g["cost_tot"] or 0),
                pratiche_con_stato=int((n_stato["n"] if n_stato else 0) or 0),
            )

    except sqlite3.OperationalError as exc:
        logger.warning("_stats_compagnie: tabelle non pronte (%s)", exc)
        stats = []
        globale = StatGlobale(0, 0, 0.0, 0.0, 0)

    return stats, globale


# --------------------------------------------------------------------------- #
#  Route statistiche
# --------------------------------------------------------------------------- #


@router.get("/statistiche", response_class=HTMLResponse)
def statistiche(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> HTMLResponse:
    """Pagina statistiche per compagnia assicurativa."""
    stats, globale = _stats_compagnie(settings.app_db_path)
    context = _common_context()
    context["stats"] = stats
    context["globale"] = globale
    return templates.TemplateResponse(request, "statistiche.html", context)


# --------------------------------------------------------------------------- #
#  Route impostazioni (policy editor)
# --------------------------------------------------------------------------- #


@router.get("/impostazioni", response_class=HTMLResponse)
def impostazioni_get(
    request: Request,
    saved: str | None = None,
    settings: Settings = Depends(get_settings_dep),
) -> HTMLResponse:
    """Editor policy bozze per categoria AI."""
    policy_repo = CategoriaPolicyRepository(db_path=settings.app_db_path)
    policies = policy_repo.get_all()
    context = _common_context()
    context["policies"] = policies
    context["policy_options"] = POLICIES
    context["policy_labels"] = POLICY_LABELS
    context["saved"] = saved
    return templates.TemplateResponse(request, "impostazioni.html", context)


@router.post("/impostazioni", response_class=HTMLResponse)
async def impostazioni_post(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> RedirectResponse:
    """Salva le policy aggiornate."""
    form = await request.form()
    policy_repo = CategoriaPolicyRepository(db_path=settings.app_db_path)
    errori: list[str] = []
    for key, value in form.items():
        if key.startswith("policy_"):
            categoria = key[len("policy_"):]
            try:
                policy_repo.set_policy(categoria, str(value))
            except ValueError as exc:
                errori.append(str(exc))
    if errori:
        logger.warning("Impostazioni: errori salvataggio: %s", errori)
    return RedirectResponse(url="/impostazioni?saved=1", status_code=303)


# --------------------------------------------------------------------------- #
#  Route cambio stato pratica
# --------------------------------------------------------------------------- #


@router.post("/pratiche/{numero}/stato")
async def pratica_cambia_stato(
    numero: int,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> RedirectResponse:
    """Aggiorna lo stato di una pratica (azione operatore dalla UI)."""
    form = await request.form()
    nuovo_stato = str(form.get("stato", "")).strip()
    note = str(form.get("note", "")).strip()

    if nuovo_stato not in STATI:
        logger.warning("Stato non valido ricevuto: %r", nuovo_stato)
        return RedirectResponse(url=f"/pratiche/{numero}", status_code=303)

    stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
    try:
        stato_repo.set_stato(
            numero,
            nuovo_stato,
            changed_by="operatore",
            note=note,
        )
        logger.info("Pratica %s: stato → %s (operatore)", numero, nuovo_stato)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore cambio stato pratica %s: %s", numero, exc)

    return RedirectResponse(url=f"/pratiche/{numero}?stato_aggiornato=1", status_code=303)
