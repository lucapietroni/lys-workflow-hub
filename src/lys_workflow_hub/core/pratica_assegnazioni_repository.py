"""Assegnazione di pratiche a utenti esterni (agenzie pratiche auto, avvocati).

Relazione many-to-many: una pratica può essere assegnata a più utenti esterni
contemporaneamente (es. agenzia E avvocato sulla stessa pratica), e un utente
esterno può avere più pratiche assegnate. Decide sempre l'admin chi assegnare
(vedi UI su `pratica_detail.html`).

Nessun riferimento diretto ai dati WinCar qui: la tabella lega solo
`pratica_numero` (intero, come in tutte le altre tabelle SQLite del progetto,
es. `pratica_stato`) a `utente_id` (FK verso `utenti`, vedi
`core/utenti_repository.py`).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Assegnazione:
    id: int
    pratica_numero: int
    utente_id: int
    assegnato_da: int | None
    assegnato_at: datetime | None


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pratica_assegnazioni (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pratica_numero  INTEGER NOT NULL,
    utente_id       INTEGER NOT NULL,
    assegnato_da    INTEGER,
    assegnato_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pratica_assegnazioni
    ON pratica_assegnazioni(pratica_numero, utente_id);

CREATE INDEX IF NOT EXISTS idx_assegnazioni_pratica
    ON pratica_assegnazioni(pratica_numero);

CREATE INDEX IF NOT EXISTS idx_assegnazioni_utente
    ON pratica_assegnazioni(utente_id);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class PraticaAssegnazioniRepository:
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
    def _row_to_assegnazione(row: sqlite3.Row) -> Assegnazione:
        d = dict(row)
        return Assegnazione(
            id=d["id"],
            pratica_numero=d["pratica_numero"],
            utente_id=d["utente_id"],
            assegnato_da=d.get("assegnato_da"),
            assegnato_at=_parse_dt(d.get("assegnato_at")),
        )

    def assegna(
        self, pratica_numero: int, utente_id: int, assegnato_da: int | None
    ) -> bool:
        """Idempotente: assegnare due volte lo stesso utente non duplica la
        riga. Ritorna True solo se questa chiamata ha creato una nuova
        assegnazione (utile per notificare l'utente una volta sola, non ad
        ogni resubmit)."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO pratica_assegnazioni "
                "(pratica_numero, utente_id, assegnato_da, assegnato_at) "
                "VALUES (?, ?, ?, ?)",
                (int(pratica_numero), int(utente_id), assegnato_da, now),
            )
            return cur.rowcount > 0

    def rimuovi(self, pratica_numero: int, utente_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM pratica_assegnazioni "
                "WHERE pratica_numero = ? AND utente_id = ?",
                (int(pratica_numero), int(utente_id)),
            )
            return cur.rowcount > 0

    def list_utente_ids_per_pratica(self, pratica_numero: int) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT utente_id FROM pratica_assegnazioni "
                "WHERE pratica_numero = ? ORDER BY assegnato_at",
                (int(pratica_numero),),
            ).fetchall()
        return [r["utente_id"] for r in rows]

    def list_pratica_numeri_per_utente(self, utente_id: int) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pratica_numero FROM pratica_assegnazioni "
                "WHERE utente_id = ? ORDER BY assegnato_at DESC",
                (int(utente_id),),
            ).fetchall()
        return [r["pratica_numero"] for r in rows]

    def mappa_utenti_per_pratica(self) -> dict[int, list[int]]:
        """Tutte le assegnazioni in un colpo solo, come dict pratica_numero
        -> lista utente_id — per l'export CSV filtrato per collaboratore
        (o per mostrare i filtri lato client), dove una query per pratica
        su potenzialmente migliaia di righe sarebbe troppo lenta."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pratica_numero, utente_id FROM pratica_assegnazioni "
                "ORDER BY assegnato_at"
            ).fetchall()
        mappa: dict[int, list[int]] = {}
        for r in rows:
            mappa.setdefault(r["pratica_numero"], []).append(r["utente_id"])
        return mappa

    def list_pratica_numeri_assegnate(self) -> list[int]:
        """Ogni pratica con almeno un'assegnazione, a chiunque — usato dal
        ruolo "supervisore" (vede tutto in sola lettura, non solo le proprie
        come `list_pratica_numeri_per_utente`). Una pratica assegnata a più
        utenti compare una sola volta, ordinata per l'assegnazione più
        recente ricevuta."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pratica_numero, MAX(assegnato_at) AS ultima "
                "FROM pratica_assegnazioni "
                "GROUP BY pratica_numero ORDER BY ultima DESC"
            ).fetchall()
        return [r["pratica_numero"] for r in rows]
