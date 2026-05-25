"""Stato del ciclo di vita delle pratiche (M5).

Tabella ``pratica_stato`` in ``data/lys_hub.db``.

Ogni pratica ha al più uno stato corrente. Ogni cambio di stato viene
aggiunto come nuova riga (storia immutabile); la riga più recente
per ``numero_pratica`` è quella corrente.

Tabella ``pec_sla_reminder``: log dei reminder SLA già inviati, per
evitare di rispammare l'operatore. La check scatta solo se non esiste
già un reminder nelle ultime ``sla_giorni // 2`` giornate.

La query SLA (pratiche con PEC senza risposta oltre soglia) si calcola
al volo via JOIN tra ``pec_inviate`` e ``mail_classificate`` (entrambe
nel medesimo DB), senza tabella separata.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Costanti stato pratica
# --------------------------------------------------------------------------- #

STATO_APERTA = "aperta"
STATO_IN_GESTIONE = "in_gestione"
STATO_PERITO_NOMINATO = "perito_nominato"
STATO_IN_LIQUIDAZIONE = "in_liquidazione"
STATO_CHIUSA = "chiusa"

STATI = (
    STATO_APERTA,
    STATO_IN_GESTIONE,
    STATO_PERITO_NOMINATO,
    STATO_IN_LIQUIDAZIONE,
    STATO_CHIUSA,
)

STATO_LABELS = {
    STATO_APERTA: "Aperta",
    STATO_IN_GESTIONE: "In gestione",
    STATO_PERITO_NOMINATO: "Perito nominato",
    STATO_IN_LIQUIDAZIONE: "In liquidazione",
    STATO_CHIUSA: "Chiusa",
}

# Classi CSS/badge per ogni stato (usate nei template Jinja2).
STATO_BADGE_CLASS = {
    STATO_APERTA: "badge-blue",
    STATO_IN_GESTIONE: "badge-yellow",
    STATO_PERITO_NOMINATO: "badge-orange",
    STATO_IN_LIQUIDAZIONE: "badge-purple",
    STATO_CHIUSA: "badge-gray",
}

# Transizioni automatiche categoria AI → stato pratica.
# Solo "upgrade": non si torna mai indietro automaticamente.
# presa_in_carico → in_gestione, nomina_perito → perito_nominato, ecc.
_AUTO_TRANSITIONS: dict[str, str] = {
    "presa_in_carico": STATO_IN_GESTIONE,
    "nomina_perito": STATO_PERITO_NOMINATO,
    "liquidazione": STATO_IN_LIQUIDAZIONE,
}


# --------------------------------------------------------------------------- #
#  Schema SQL
# --------------------------------------------------------------------------- #

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pratica_stato (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pratica_numero  INTEGER NOT NULL,
    stato           TEXT    NOT NULL DEFAULT 'aperta',
    changed_at      TEXT    NOT NULL,
    changed_by      TEXT    NOT NULL DEFAULT 'sistema',
    note            TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pratica_stato_numero
    ON pratica_stato(pratica_numero, changed_at DESC);

CREATE TABLE IF NOT EXISTS pec_sla_reminder (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pec_inviata_id  INTEGER NOT NULL,
    reminded_at     TEXT    NOT NULL,
    tipo            TEXT    NOT NULL DEFAULT 'push'
);

CREATE INDEX IF NOT EXISTS idx_sla_reminder_pec
    ON pec_sla_reminder(pec_inviata_id, reminded_at DESC);
"""


# --------------------------------------------------------------------------- #
#  Modelli
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PraticaStato:
    """Una riga di stato (record immutabile di un cambio stato)."""

    id: int | None
    pratica_numero: int
    stato: str
    changed_at: datetime
    changed_by: str
    note: str

    @property
    def stato_label(self) -> str:
        return STATO_LABELS.get(self.stato, self.stato)

    @property
    def badge_class(self) -> str:
        return STATO_BADGE_CLASS.get(self.stato, "badge-gray")


@dataclass(frozen=True)
class SlaAlert:
    """Una PEC inviata senza risposta oltre la soglia SLA."""

    pec_inviata_id: int
    pratica_numero: int
    compagnia_nome: str
    data_invio: datetime
    giorni_attesa: int
    already_reminded: bool


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


class PraticaStatoRepository:
    """CRUD per stato pratica + SLA tracker."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_stato(row: sqlite3.Row) -> PraticaStato:
        try:
            changed_at = datetime.fromisoformat(row["changed_at"])
        except (ValueError, TypeError):
            changed_at = datetime.now()
        return PraticaStato(
            id=row["id"],
            pratica_numero=int(row["pratica_numero"]),
            stato=row["stato"],
            changed_at=changed_at,
            changed_by=row["changed_by"] or "sistema",
            note=row["note"] or "",
        )

    # ---------------------------------------------------------------- lettura -

    def get_stato(self, pratica_numero: int) -> PraticaStato | None:
        """Stato corrente (riga più recente) o None se mai impostato."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pratica_stato WHERE pratica_numero = ? "
                "ORDER BY changed_at DESC, id DESC LIMIT 1",
                (int(pratica_numero),),
            ).fetchone()
        return self._row_to_stato(row) if row else None

    def storia(self, pratica_numero: int, limit: int = 20) -> list[PraticaStato]:
        """Tutti i cambi di stato per una pratica (più recenti prima)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pratica_stato WHERE pratica_numero = ? "
                "ORDER BY changed_at DESC, id DESC LIMIT ?",
                (int(pratica_numero), int(limit)),
            ).fetchall()
        return [self._row_to_stato(r) for r in rows]

    def count_by_stato(self) -> dict[str, int]:
        """Conta le pratiche per stato corrente (per KPI dashboard).

        Usa una subquery per prendere solo la riga più recente per pratica.
        """
        sql = """
            SELECT stato, COUNT(*) AS n
            FROM (
                SELECT pratica_numero,
                       stato,
                       ROW_NUMBER() OVER (
                           PARTITION BY pratica_numero
                           ORDER BY changed_at DESC, id DESC
                       ) AS rn
                FROM pratica_stato
            )
            WHERE rn = 1
            GROUP BY stato
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(sql).fetchall()
            return {r["stato"]: int(r["n"]) for r in rows}
        except Exception as exc:  # noqa: BLE001
            logger.warning("count_by_stato fallito: %s", exc)
            return {}

    # --------------------------------------------------------------- scrittura -

    def set_stato(
        self,
        pratica_numero: int,
        stato: str,
        *,
        changed_by: str = "operatore",
        note: str = "",
    ) -> PraticaStato:
        """Aggiunge una riga di cambio stato (storicizzato, niente UPDATE)."""
        if stato not in STATI:
            raise ValueError(f"Stato non valido: {stato!r}")
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pratica_stato "
                "(pratica_numero, stato, changed_at, changed_by, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(pratica_numero), stato, now_iso, changed_by, note),
            )
        return self.get_stato(int(pratica_numero))  # type: ignore[return-value]

    def auto_transition(
        self, pratica_numero: int, categoria_ai: str
    ) -> PraticaStato | None:
        """Aggiorna lo stato se la categoria AI implica una transizione.

        Regole:
        - Esegue solo upgrade (indice in STATI crescente).
        - Non transita mai da CHIUSA.
        - Se nessuna transizione applicabile, ritorna lo stato corrente
          senza scrivere nulla.
        """
        target = _AUTO_TRANSITIONS.get(categoria_ai)
        if target is None:
            return self.get_stato(pratica_numero)

        current = self.get_stato(pratica_numero)
        current_stato = current.stato if current else STATO_APERTA

        if current_stato == STATO_CHIUSA:
            return current

        ordine = list(STATI)
        current_idx = ordine.index(current_stato) if current_stato in ordine else 0
        target_idx = ordine.index(target) if target in ordine else 0

        if target_idx <= current_idx:
            return current  # niente downgrade

        return self.set_stato(
            pratica_numero,
            target,
            changed_by="sistema",
            note=f"Auto-transizione da classificazione AI: {categoria_ai}",
        )

    # -------------------------------------------------------------------- SLA -

    def lista_sla_alerts(
        self, sla_giorni: int = 15, limit: int = 100
    ) -> list[SlaAlert]:
        """PEC inviate senza risposta oltre ``sla_giorni``.

        Una PEC è "senza risposta" se non esiste nessuna riga in
        ``mail_classificate`` con ``pec_inviata_id`` corrispondente.

        ``already_reminded`` è True se è già stato inviato un reminder
        nelle ultime ``sla_giorni // 2`` giornate.
        """
        soglia_invio = (
            datetime.now() - timedelta(days=sla_giorni)
        ).isoformat(timespec="seconds")
        cooldown_giorni = max(sla_giorni // 2, 1)
        cooldown_since = (
            datetime.now() - timedelta(days=cooldown_giorni)
        ).isoformat(timespec="seconds")

        sql = """
            SELECT
                p.id                AS pec_inviata_id,
                p.numero_pratica,
                p.compagnia_nome,
                p.data_invio,
                CAST(
                    (julianday('now') - julianday(p.data_invio))
                    AS INTEGER
                )                   AS giorni_attesa,
                (
                    SELECT COUNT(*)
                    FROM pec_sla_reminder r
                    WHERE r.pec_inviata_id = p.id
                      AND r.reminded_at    > :cooldown_since
                ) AS recent_reminders
            FROM pec_inviate p
            LEFT JOIN mail_classificate m ON m.pec_inviata_id = p.id
            WHERE p.esito IN ('OK', 'DRY_RUN')
              AND p.data_invio <= :soglia_invio
              AND m.id IS NULL
            GROUP BY p.id
            ORDER BY p.data_invio ASC
            LIMIT :lim
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    sql,
                    {
                        "soglia_invio": soglia_invio,
                        "cooldown_since": cooldown_since,
                        "lim": int(limit),
                    },
                ).fetchall()
        except sqlite3.OperationalError as exc:
            # Tabelle non ancora create (fresh install prima del primo polling).
            logger.debug("lista_sla_alerts: tabelle non disponibili (%s)", exc)
            return []

        alerts = []
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["data_invio"])
            except (ValueError, TypeError):
                dt = datetime.now()
            alerts.append(
                SlaAlert(
                    pec_inviata_id=int(r["pec_inviata_id"]),
                    pratica_numero=int(r["numero_pratica"]),
                    compagnia_nome=r["compagnia_nome"] or "",
                    data_invio=dt,
                    giorni_attesa=int(r["giorni_attesa"] or 0),
                    already_reminded=int(r["recent_reminders"] or 0) > 0,
                )
            )
        return alerts

    def count_sla_breach(self, sla_giorni: int = 15) -> int:
        """Numero di PEC in SLA breach (per KPI home). Silenzioso su errori."""
        try:
            return len(self.lista_sla_alerts(sla_giorni=sla_giorni))
        except Exception as exc:  # noqa: BLE001
            logger.debug("count_sla_breach fallito: %s", exc)
            return 0

    def log_sla_reminder(self, pec_inviata_id: int, tipo: str = "push") -> None:
        """Registra che il reminder SLA per questa PEC è già stato inviato."""
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pec_sla_reminder (pec_inviata_id, reminded_at, tipo) "
                "VALUES (?, ?, ?)",
                (int(pec_inviata_id), now_iso, tipo),
            )
