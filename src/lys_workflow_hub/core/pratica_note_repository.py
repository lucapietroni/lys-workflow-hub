"""Note di collaborazione condivise per pratica (v3.0 fase 4).

Thread unico per pratica (non separato per utente): admin e collaboratori
esterni assegnati scrivono nello stesso elenco cronologico — es. "preso
app.to con perito", "servono foto lavorazione", "serve preventivo".

`autore_nome` è uno snapshot del nome dell'autore al momento dell'invio
(non un JOIN live su `utenti`): la nota resta leggibile anche se l'utente
viene poi rinominato o disattivato, e non serve toccare un secondo DB/tabella
per renderizzare il thread.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Nota:
    id: int
    pratica_numero: int
    utente_id: int
    autore_nome: str
    testo: str
    created_at: datetime | None


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pratica_note (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pratica_numero  INTEGER NOT NULL,
    utente_id       INTEGER NOT NULL,
    autore_nome     TEXT NOT NULL,
    testo           TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pratica_note_pratica
    ON pratica_note(pratica_numero);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class PraticaNoteRepository:
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
    def _row_to_nota(row: sqlite3.Row) -> Nota:
        d = dict(row)
        return Nota(
            id=d["id"],
            pratica_numero=d["pratica_numero"],
            utente_id=d["utente_id"],
            autore_nome=d["autore_nome"],
            testo=d["testo"],
            created_at=_parse_dt(d.get("created_at")),
        )

    def add(self, pratica_numero: int, utente_id: int, autore_nome: str, testo: str) -> Nota:
        testo = testo.strip()
        if not testo:
            raise ValueError("Il testo della nota non può essere vuoto.")
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO pratica_note "
                "(pratica_numero, utente_id, autore_nome, testo, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(pratica_numero), int(utente_id), autore_nome, testo, now),
            )
            row = conn.execute(
                "SELECT * FROM pratica_note WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return self._row_to_nota(row)

    def list_per_pratica(self, pratica_numero: int) -> list[Nota]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pratica_note WHERE pratica_numero = ? ORDER BY created_at",
                (int(pratica_numero),),
            ).fetchall()
        return [self._row_to_nota(r) for r in rows]

    def update(self, nota_id: int, pratica_numero: int, nuovo_testo: str) -> bool:
        """`pratica_numero` obbligatorio nel WHERE: stesso motivo IDOR di
        `PraticaEventiRepository.delete` — senza, si potrebbe modificare la
        nota di un'altra pratica indovinando/incrementando l'id."""
        nuovo_testo = nuovo_testo.strip()
        if not nuovo_testo:
            raise ValueError("Il testo della nota non può essere vuoto.")
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE pratica_note SET testo = ? WHERE id = ? AND pratica_numero = ?",
                (nuovo_testo, int(nota_id), int(pratica_numero)),
            )
            return cur.rowcount > 0

    def delete(self, nota_id: int, pratica_numero: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM pratica_note WHERE id = ? AND pratica_numero = ?",
                (int(nota_id), int(pratica_numero)),
            )
            return cur.rowcount > 0
