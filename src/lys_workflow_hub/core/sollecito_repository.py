"""Repository dei solleciti PEC per escalation SLA (M6.1).

Tabella ``pec_solleciti`` in ``data/lys_hub.db``.

Un sollecito è una PEC outbound generata automaticamente quando una PEC
inviata non riceve risposta entro le soglie SLA configurate. È distinto
dai Draft M4 (risposte a mail in arrivo): qui generiamo noi l'iniziativa.

Ciclo di vita:
  pending  → creato dal polling, in attesa di revisione/invio dall'operatore
  sent     → inviato
  cancelled → annullato (es. la risposta è arrivata nel frattempo)

Idempotenza: (pec_inviata_id, livello) è UNIQUE — il polling non crea
duplicati anche se eseguito più volte.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


logger = logging.getLogger(__name__)


SOL_PENDING   = "pending"
SOL_SENT      = "sent"
SOL_CANCELLED = "cancelled"

SOL_STATI = (SOL_PENDING, SOL_SENT, SOL_CANCELLED)

SOL_LABELS = {
    SOL_PENDING:   "Da inviare",
    SOL_SENT:      "Inviato",
    SOL_CANCELLED: "Annullato",
}


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pec_solleciti (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pec_inviata_id    INTEGER NOT NULL,
    pratica_numero    INTEGER NOT NULL,
    livello           INTEGER NOT NULL DEFAULT 1,
    status            TEXT    NOT NULL DEFAULT 'pending',
    to_address        TEXT    NOT NULL DEFAULT '',
    subject           TEXT    NOT NULL DEFAULT '',
    body_html         TEXT    NOT NULL DEFAULT '',
    created_at        TEXT    NOT NULL,
    sent_at           TEXT,
    sent_eml_path     TEXT    NOT NULL DEFAULT '',
    cancel_reason     TEXT    NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pec_solleciti_pec_livello
    ON pec_solleciti(pec_inviata_id, livello);

CREATE INDEX IF NOT EXISTS idx_pec_solleciti_pratica
    ON pec_solleciti(pratica_numero);

CREATE INDEX IF NOT EXISTS idx_pec_solleciti_status
    ON pec_solleciti(status);
"""


# --------------------------------------------------------------------------- #
#  Modello
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Sollecito:
    id: int | None
    pec_inviata_id: int
    pratica_numero: int
    livello: int
    status: str
    to_address: str
    subject: str
    body_html: str
    created_at: datetime
    sent_at: datetime | None = None
    sent_eml_path: str = ""
    cancel_reason: str = ""

    @property
    def status_label(self) -> str:
        return SOL_LABELS.get(self.status, self.status)

    @property
    def is_editable(self) -> bool:
        return self.status == SOL_PENDING


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


class SollecitoRepository:
    """CRUD per ``pec_solleciti``."""

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
    def _row_to_sol(row: sqlite3.Row) -> Sollecito:
        return Sollecito(
            id=row["id"],
            pec_inviata_id=int(row["pec_inviata_id"]),
            pratica_numero=int(row["pratica_numero"]),
            livello=int(row["livello"]),
            status=row["status"] or SOL_PENDING,
            to_address=row["to_address"] or "",
            subject=row["subject"] or "",
            body_html=row["body_html"] or "",
            created_at=_parse_dt(row["created_at"]) or datetime.now(),
            sent_at=_parse_dt(row["sent_at"]),
            sent_eml_path=row["sent_eml_path"] or "",
            cancel_reason=row["cancel_reason"] or "",
        )

    # ------------------------------------------------------------------ CRUD -

    def insert_sollecito(
        self,
        *,
        pec_inviata_id: int,
        pratica_numero: int,
        livello: int,
        to_address: str,
        subject: str,
        body_html: str,
    ) -> Sollecito:
        """Crea un nuovo sollecito in stato ``pending``.

        Idempotente: se esiste già per (pec_inviata_id, livello),
        restituisce quello esistente senza modificarlo.
        """
        existing = self.get_by_pec_livello(pec_inviata_id, livello)
        if existing is not None:
            return existing
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO pec_solleciti "
                "(pec_inviata_id, pratica_numero, livello, status, "
                " to_address, subject, body_html, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(pec_inviata_id),
                    int(pratica_numero),
                    int(livello),
                    SOL_PENDING,
                    to_address,
                    subject,
                    body_html,
                    _iso_now(),
                ),
            )
            new_id = cur.lastrowid
        return self.get_sollecito(int(new_id))  # type: ignore[return-value]

    def get_sollecito(self, sol_id: int) -> Sollecito | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pec_solleciti WHERE id = ?", (int(sol_id),)
            ).fetchone()
        return self._row_to_sol(row) if row else None

    def get_by_pec_livello(
        self, pec_inviata_id: int, livello: int
    ) -> Sollecito | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pec_solleciti "
                "WHERE pec_inviata_id = ? AND livello = ?",
                (int(pec_inviata_id), int(livello)),
            ).fetchone()
        return self._row_to_sol(row) if row else None

    def list_by_status(
        self, status: str, *, limit: int = 200
    ) -> list[Sollecito]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pec_solleciti WHERE status = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, int(limit)),
            ).fetchall()
        return [self._row_to_sol(r) for r in rows]

    def list_per_pratica(self, pratica_numero: int) -> list[Sollecito]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pec_solleciti WHERE pratica_numero = ? "
                "ORDER BY livello ASC, created_at DESC",
                (int(pratica_numero),),
            ).fetchall()
        return [self._row_to_sol(r) for r in rows]

    def update_body(
        self,
        sol_id: int,
        *,
        subject: str,
        body_html: str,
        to_address: str,
    ) -> Sollecito:
        """Aggiorna testo/destinatario (solo se pending)."""
        sol = self.get_sollecito(sol_id)
        if sol is None:
            raise ValueError(f"Sollecito id={sol_id} non trovato")
        if not sol.is_editable:
            raise ValueError(
                f"Sollecito id={sol_id} non modificabile (status={sol.status})"
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE pec_solleciti "
                "SET subject = ?, body_html = ?, to_address = ? "
                "WHERE id = ?",
                (subject, body_html, to_address, int(sol_id)),
            )
        return self.get_sollecito(sol_id)  # type: ignore[return-value]

    def mark_sent(self, sol_id: int, *, sent_eml_path: str = "") -> Sollecito:
        sol = self.get_sollecito(sol_id)
        if sol is None:
            raise ValueError(f"Sollecito id={sol_id} non trovato")
        if sol.status == SOL_SENT:
            return sol  # idempotente
        now = _iso_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE pec_solleciti "
                "SET status = ?, sent_at = ?, sent_eml_path = ? "
                "WHERE id = ?",
                (SOL_SENT, now, sent_eml_path, int(sol_id)),
            )
        return self.get_sollecito(sol_id)  # type: ignore[return-value]

    def mark_cancelled(self, sol_id: int, *, reason: str = "") -> Sollecito:
        sol = self.get_sollecito(sol_id)
        if sol is None:
            raise ValueError(f"Sollecito id={sol_id} non trovato")
        if sol.status == SOL_SENT:
            raise ValueError(
                f"Sollecito id={sol_id} già inviato, non annullabile"
            )
        if sol.status == SOL_CANCELLED:
            return sol  # idempotente
        with self._connect() as conn:
            conn.execute(
                "UPDATE pec_solleciti SET status = ?, cancel_reason = ? "
                "WHERE id = ?",
                (SOL_CANCELLED, reason, int(sol_id)),
            )
        return self.get_sollecito(sol_id)  # type: ignore[return-value]

    def conta_pending(self) -> int:
        """Solleciti in stato pending (per badge UI)."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM pec_solleciti "
                    "WHERE status = ?",
                    (SOL_PENDING,),
                ).fetchone()
            return int(row["n"] or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("conta_pending solleciti: %s", exc)
            return 0

    def conta_per_status(self) -> dict[str, int]:
        out = {s: 0 for s in SOL_STATI}
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n "
                    "FROM pec_solleciti GROUP BY status"
                ).fetchall()
            for r in rows:
                s = r["status"] or SOL_PENDING
                if s in out:
                    out[s] = int(r["n"])
        except Exception as exc:  # noqa: BLE001
            logger.debug("conta_per_status solleciti: %s", exc)
        return out
