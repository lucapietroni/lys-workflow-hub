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
STATO_PERIZIATA = "periziata"
STATO_IN_TRATTATIVA = "in_trattativa"
STATO_IN_LIQUIDAZIONE = "in_liquidazione"
STATO_CHIUSA = "chiusa"

STATI = (
    STATO_APERTA,
    STATO_IN_GESTIONE,
    STATO_PERITO_NOMINATO,
    STATO_PERIZIATA,
    STATO_IN_TRATTATIVA,
    STATO_IN_LIQUIDAZIONE,
    STATO_CHIUSA,
)

STATO_LABELS = {
    STATO_APERTA: "Aperta",
    STATO_IN_GESTIONE: "In gestione",
    STATO_PERITO_NOMINATO: "Perito nominato",
    STATO_PERIZIATA: "Periziata",
    STATO_IN_TRATTATIVA: "In trattativa",
    STATO_IN_LIQUIDAZIONE: "In liquidazione",
    STATO_CHIUSA: "Chiusa",
}

# Classi CSS/badge per ogni stato (usate nei template Jinja2).
STATO_BADGE_CLASS = {
    STATO_APERTA: "badge-blue",
    STATO_IN_GESTIONE: "badge-yellow",
    STATO_PERITO_NOMINATO: "badge-orange",
    STATO_PERIZIATA: "badge-teal",
    STATO_IN_TRATTATIVA: "badge-pink",
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
    tipo            TEXT    NOT NULL DEFAULT 'push',
    livello         INTEGER NOT NULL DEFAULT 1
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


@dataclass(frozen=True)
class SlaEscalationAlert:
    """Un livello di escalation da gestire per una PEC senza risposta (M6.1).

    Un singolo ``pec_inviata_id`` può generare più alert (uno per livello
    breached e non ancora loggato in ``pec_sla_reminder``).
    """

    pec_inviata_id: int
    pratica_numero: int
    compagnia_nome: str
    destinatario_pec: str   # indirizzo PEC della compagnia
    oggetto_originale: str  # subject della PEC originale inviata
    data_invio: datetime
    giorni_attesa: int
    livello_richiesto: int  # 1 = sollecito, 2 = formale, 3 = diffida


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
            # Migrazione M6.1: aggiunge colonna livello se non presente
            # (DB già esistenti dalla M5 non l'hanno).
            try:
                conn.execute(
                    "ALTER TABLE pec_sla_reminder "
                    "ADD COLUMN livello INTEGER NOT NULL DEFAULT 1"
                )
            except sqlite3.OperationalError:
                pass  # colonna già presente

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

    def stati_correnti(self) -> dict[int, str]:
        """Stato corrente di OGNI pratica con almeno un cambio registrato,
        in un colpo solo — per l'export CSV filtrato per stato, dove
        servirebbe altrimenti una query per numero su potenzialmente
        migliaia di pratiche. Stessa subquery "riga più recente per
        pratica" di `count_by_stato`. Le pratiche assenti dal dict
        risultante non hanno mai avuto un cambio di stato: il chiamante
        applica il default STATO_APERTA, come ovunque nell'app."""
        sql = """
            SELECT pratica_numero, stato
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
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(sql).fetchall()
            return {int(r["pratica_numero"]): r["stato"] for r in rows}
        except Exception as exc:  # noqa: BLE001
            logger.warning("stati_correnti fallito: %s", exc)
            return {}

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

    def log_sla_reminder(
        self, pec_inviata_id: int, tipo: str = "push", livello: int = 1
    ) -> None:
        """Registra che il reminder SLA per questa PEC è già stato inviato.

        ``livello``: 1 = sollecito, 2 = formale, 3 = diffida (M6.1).
        """
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pec_sla_reminder "
                "(pec_inviata_id, reminded_at, tipo, livello) "
                "VALUES (?, ?, ?, ?)",
                (int(pec_inviata_id), now_iso, tipo, int(livello)),
            )

    def livelli_already_sent(self, pec_inviata_id: int) -> set[int]:
        """Set dei livelli escalation già loggati per questa PEC."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT livello FROM pec_sla_reminder WHERE pec_inviata_id = ?",
                    (int(pec_inviata_id),),
                ).fetchall()
            return {int(r["livello"]) for r in rows}
        except sqlite3.OperationalError:
            return set()

    def lista_sla_escalation(
        self,
        soglie: dict[int, int],
        limit: int = 100,
    ) -> list[SlaEscalationAlert]:
        """PEC senza risposta con livelli di escalation da gestire (M6.1).

        ``soglie``: dizionario {livello: giorni_soglia}, es.
        ``{1: 15, 2: 30, 3: 45}``. Ritorna un ``SlaEscalationAlert`` per
        ogni coppia (pec_id, livello) dove la soglia è superata e il livello
        non è ancora stato loggato in ``pec_sla_reminder``.

        Livelli con soglia 0 vengono ignorati (disabilitati).
        Ordinato per data_invio ASC, poi livello ASC.
        """
        soglie_attive = {l: g for l, g in soglie.items() if g > 0}
        if not soglie_attive:
            return []

        min_giorni = min(soglie_attive.values())
        soglia_min_iso = (
            datetime.now() - timedelta(days=min_giorni)
        ).isoformat(timespec="seconds")

        sql = """
            SELECT
                p.id                AS pec_inviata_id,
                p.numero_pratica,
                p.compagnia_nome,
                p.destinatario_pec,
                p.oggetto,
                p.data_invio,
                CAST(
                    (julianday('now') - julianday(p.data_invio))
                    AS INTEGER
                )                   AS giorni_attesa
            FROM pec_inviate p
            LEFT JOIN mail_classificate m ON m.pec_inviata_id = p.id
            WHERE p.esito IN ('OK', 'DRY_RUN')
              AND p.data_invio <= :soglia_min_iso
              AND m.id IS NULL
            GROUP BY p.id
            ORDER BY p.data_invio ASC
            LIMIT :lim
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    sql,
                    {"soglia_min_iso": soglia_min_iso, "lim": int(limit)},
                ).fetchall()

                if not rows:
                    return []

                pec_ids = [int(r["pec_inviata_id"]) for r in rows]
                placeholders = ",".join("?" * len(pec_ids))
                reminder_rows = conn.execute(
                    f"SELECT pec_inviata_id, livello "
                    f"FROM pec_sla_reminder "
                    f"WHERE pec_inviata_id IN ({placeholders})",
                    pec_ids,
                ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.debug("lista_sla_escalation: tabelle non disponibili (%s)", exc)
            return []

        # Set dei livelli già loggati per PEC.
        already_sent: dict[int, set[int]] = {}
        for rr in reminder_rows:
            pid = int(rr["pec_inviata_id"])
            already_sent.setdefault(pid, set()).add(int(rr["livello"]))

        alerts: list[SlaEscalationAlert] = []
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["data_invio"])
            except (ValueError, TypeError):
                dt = datetime.now()

            pid = int(r["pec_inviata_id"])
            giorni = int(r["giorni_attesa"] or 0)
            sent_levels = already_sent.get(pid, set())

            for livello, soglia_giorni in sorted(soglie_attive.items()):
                if giorni >= soglia_giorni and livello not in sent_levels:
                    alerts.append(
                        SlaEscalationAlert(
                            pec_inviata_id=pid,
                            pratica_numero=int(r["numero_pratica"]),
                            compagnia_nome=r["compagnia_nome"] or "",
                            destinatario_pec=r["destinatario_pec"] or "",
                            oggetto_originale=r["oggetto"] or "",
                            data_invio=dt,
                            giorni_attesa=giorni,
                            livello_richiesto=livello,
                        )
                    )

        return alerts
