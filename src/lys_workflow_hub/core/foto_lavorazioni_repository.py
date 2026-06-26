"""Log SQLite delle foto lavorazioni processate dall'inbox Syncthing."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class FotoRecord:
    id: int
    filename_originale: str
    targa_riconosciuta: str
    pratica_numero: int | None
    percorso_fallback: str
    percorso_pratica: str
    stato: str  # ok / ok_no_pratica / targa_non_trovata / errore / heic
    errore: str
    created_at: str


class FotoLavorazioniRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_table()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS foto_lavorazioni (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename_originale  TEXT    NOT NULL,
                    targa_riconosciuta  TEXT    DEFAULT '',
                    pratica_numero      INTEGER,
                    percorso_fallback   TEXT    DEFAULT '',
                    percorso_pratica    TEXT    DEFAULT '',
                    stato               TEXT    NOT NULL,
                    errore              TEXT    DEFAULT '',
                    created_at          TEXT    DEFAULT (datetime('now'))
                )
            """)

    def log_foto(
        self,
        *,
        filename: str,
        targa: str = "",
        pratica_numero: int | None = None,
        percorso_fallback: str = "",
        percorso_pratica: str = "",
        stato: str,
        errore: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO foto_lavorazioni
                   (filename_originale, targa_riconosciuta, pratica_numero,
                    percorso_fallback, percorso_pratica, stato, errore)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (filename, targa, pratica_numero, percorso_fallback,
                 percorso_pratica, stato, errore),
            )
            return cur.lastrowid

    def list_recenti(self, limit: int = 100) -> list[FotoRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM foto_lavorazioni ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FotoRecord:
        return FotoRecord(
            id=row["id"],
            filename_originale=row["filename_originale"],
            targa_riconosciuta=row["targa_riconosciuta"] or "",
            pratica_numero=row["pratica_numero"],
            percorso_fallback=row["percorso_fallback"] or "",
            percorso_pratica=row["percorso_pratica"] or "",
            stato=row["stato"],
            errore=row["errore"] or "",
            created_at=row["created_at"] or "",
        )
