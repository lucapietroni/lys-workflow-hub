"""Calendario condiviso per pratica (v3.0 fase 4).

Elenco di eventi (es. data perizia, appuntamento) associati a una pratica,
visibili e gestibili sia dall'admin che dai collaboratori esterni assegnati.
Nessun concetto di "proprietario esclusivo": chi ha accesso alla pratica può
aggiungere o eliminare un evento, coerente con l'uso previsto (calendario
condiviso leggero, non un sistema di permessi granulari).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Evento:
    id: int
    pratica_numero: int
    titolo: str
    data_evento: date | None
    creato_da: int
    creato_da_nome: str
    created_at: datetime | None


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pratica_eventi (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pratica_numero  INTEGER NOT NULL,
    titolo          TEXT NOT NULL,
    data_evento     TEXT NOT NULL,
    creato_da       INTEGER NOT NULL,
    creato_da_nome  TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pratica_eventi_pratica
    ON pratica_eventi(pratica_numero);

-- Dedup reminder "il giorno prima" (v3.0 fase 5, parte B): una riga per
-- evento notificato, cosi' `scripts/send_event_reminders.py` (schedulato
-- una volta al giorno) non rispedisce lo stesso reminder ad ogni run.
-- Stesso pattern di `pec_sla_reminder` in pratica_stato_repository.py.
CREATE TABLE IF NOT EXISTS pratica_eventi_reminder (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    evento_id   INTEGER NOT NULL,
    reminded_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pratica_eventi_reminder_evento
    ON pratica_eventi_reminder(evento_id);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class PraticaEventiRepository:
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
    def _row_to_evento(row: sqlite3.Row) -> Evento:
        d = dict(row)
        return Evento(
            id=d["id"],
            pratica_numero=d["pratica_numero"],
            titolo=d["titolo"],
            data_evento=_parse_date(d.get("data_evento")),
            creato_da=d["creato_da"],
            creato_da_nome=d["creato_da_nome"],
            created_at=_parse_dt(d.get("created_at")),
        )

    def add(
        self,
        pratica_numero: int,
        titolo: str,
        data_evento: date,
        creato_da: int,
        creato_da_nome: str,
    ) -> Evento:
        titolo = titolo.strip()
        if not titolo:
            raise ValueError("Il titolo dell'evento non può essere vuoto.")
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO pratica_eventi "
                "(pratica_numero, titolo, data_evento, creato_da, creato_da_nome, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    int(pratica_numero),
                    titolo,
                    data_evento.isoformat(),
                    int(creato_da),
                    creato_da_nome,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM pratica_eventi WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return self._row_to_evento(row)

    def list_per_pratica(self, pratica_numero: int) -> list[Evento]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pratica_eventi WHERE pratica_numero = ? "
                "ORDER BY data_evento, created_at",
                (int(pratica_numero),),
            ).fetchall()
        return [self._row_to_evento(r) for r in rows]

    def list_prossimi(
        self, entro_giorni: int = 7, pratica_numeri: list[int] | None = None
    ) -> list[Evento]:
        """Eventi tra oggi e oggi+`entro_giorni`, opzionalmente filtrati a un
        sottoinsieme di pratiche (usato dal portale esterno per mostrare solo
        gli eventi delle proprie pratiche assegnate). `pratica_numeri=None`
        significa "tutte" (vista admin); `pratica_numeri=[]` ritorna sempre
        lista vuota senza interrogare il DB."""
        if pratica_numeri is not None and not pratica_numeri:
            return []
        query = (
            "SELECT * FROM pratica_eventi "
            "WHERE date(data_evento) BETWEEN date('now') AND date('now', ?) "
        )
        params: list = [f"+{int(entro_giorni)} days"]
        if pratica_numeri is not None:
            placeholders = ",".join("?" for _ in pratica_numeri)
            query += f"AND pratica_numero IN ({placeholders}) "
            params.extend(int(n) for n in pratica_numeri)
        query += "ORDER BY data_evento, created_at LIMIT 20"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_evento(r) for r in rows]

    def delete(self, evento_id: int, pratica_numero: int) -> bool:
        """`pratica_numero` è obbligatorio: senza, un utente esterno con
        accesso alla pratica A potrebbe cancellare un evento della pratica B
        semplicemente indovinando/incrementando l'id (IDOR)."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM pratica_eventi WHERE id = ? AND pratica_numero = ?",
                (int(evento_id), int(pratica_numero)),
            )
            return cur.rowcount > 0

    def list_mese(
        self, anno: int, mese: int, pratica_numeri: list[int] | None = None
    ) -> list[Evento]:
        """Tutti gli eventi del mese indicato, opzionalmente filtrati a un
        sottoinsieme di pratiche (portale esterno: solo le proprie
        assegnate). `pratica_numeri=None` = tutte (vista admin);
        `pratica_numeri=[]` ritorna sempre lista vuota."""
        if pratica_numeri is not None and not pratica_numeri:
            return []
        mese_str = f"{int(anno):04d}-{int(mese):02d}"
        query = (
            "SELECT * FROM pratica_eventi "
            "WHERE substr(data_evento, 1, 7) = ? "
        )
        params: list = [mese_str]
        if pratica_numeri is not None:
            placeholders = ",".join("?" for _ in pratica_numeri)
            query += f"AND pratica_numero IN ({placeholders}) "
            params.extend(int(n) for n in pratica_numeri)
        query += "ORDER BY data_evento, created_at"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_evento(r) for r in rows]

    # -- reminder "il giorno prima" (v3.0 fase 5, parte B) ------------------

    def list_domani(self) -> list[Evento]:
        """Eventi con data = domani. Usato dallo script schedulato
        `send_event_reminders.py`, eseguito una volta al giorno."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pratica_eventi WHERE date(data_evento) = date('now', '+1 day') "
                "ORDER BY pratica_numero"
            ).fetchall()
        return [self._row_to_evento(r) for r in rows]

    def reminder_gia_inviato(self, evento_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM pratica_eventi_reminder WHERE evento_id = ?",
                (int(evento_id),),
            ).fetchone()
        return row is not None

    def segna_reminder_inviato(self, evento_id: int) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pratica_eventi_reminder (evento_id, reminded_at) "
                "VALUES (?, ?)",
                (int(evento_id), now),
            )
