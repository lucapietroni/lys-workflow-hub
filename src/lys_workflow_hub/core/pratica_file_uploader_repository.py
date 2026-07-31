"""Traccia chi ha caricato ciascun file (foto/documento) di una pratica.

I file vivono solo su disco (cartelle WinCar, vedi `pratica_files.py`) —
nessun metadato per file finché non serve sapere CHI l'ha caricato. Serve
per una sola cosa: permettere a un collaboratore esterno di eliminare i
file che ha caricato lui, mai quelli caricati dall'admin o da un altro
collaboratore. Un file caricato PRIMA dell'introduzione di questa feature
non ha nessuna riga qui — resta eliminabile solo dall'admin, comportamento
invariato (nessuna riga = nessun proprietario noto, mai un falso permesso).

`path` (l'`Allegato.path` assoluto, sempre univoco: `save_upload()` non
sovrascrive mai un file esistente) è la chiave naturale, non serve un
numero pratica come parte della chiave — usato comunque come colonna per
poter ripulire/interrogare per pratica senza un JOIN.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pratica_file_uploader (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pratica_numero    INTEGER NOT NULL,
    path              TEXT NOT NULL,
    caricato_da       INTEGER NOT NULL,
    caricato_da_nome  TEXT NOT NULL,
    caricato_il       TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pratica_file_uploader_path
    ON pratica_file_uploader(path);
CREATE INDEX IF NOT EXISTS idx_pratica_file_uploader_pratica
    ON pratica_file_uploader(pratica_numero);
"""


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class FileUploader:
    id: int
    pratica_numero: int
    path: str
    caricato_da: int
    caricato_da_nome: str
    caricato_il: datetime | None


class PraticaFileUploaderRepository:
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

    def registra(
        self, pratica_numero: int, path: Path, *, caricato_da: int, caricato_da_nome: str
    ) -> None:
        """Best-effort dal punto di vista del chiamante: se fallisce, il file
        resta comunque salvato su disco (già scritto prima di questa
        chiamata) — semplicemente non sarà eliminabile dal suo autore finché
        non lo elimina un admin. `INSERT OR REPLACE` sul path univoco: non
        dovrebbe mai capitare (nomi sempre nuovi, timestampati), ma non deve
        sollevare se per qualunque motivo capitasse."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pratica_file_uploader "
                "(pratica_numero, path, caricato_da, caricato_da_nome, caricato_il) "
                "VALUES (?, ?, ?, ?, ?)",
                (pratica_numero, str(path), caricato_da, caricato_da_nome, _iso_now()),
            )

    def caricato_da(self, path: Path) -> int | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT caricato_da FROM pratica_file_uploader WHERE path = ?",
                (str(path),),
            )
            row = cur.fetchone()
            return int(row["caricato_da"]) if row else None

    def eliminabile_da(self, pratica_numero: int, path: Path, utente_id: int) -> bool:
        """True solo se `path` è tracciato PROPRIO sotto `pratica_numero` e
        caricato da `utente_id` — a differenza di `caricato_da()`, non basta
        che l'utente sia l'autore: la pratica nell'URL deve combaciare con
        quella della riga, per non dipendere dal fatto che il chiamante poi
        rivalidi comunque il path con `scan_allegati(numero)`."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT 1 FROM pratica_file_uploader "
                "WHERE path = ? AND pratica_numero = ? AND caricato_da = ?",
                (str(path), pratica_numero, utente_id),
            )
            return cur.fetchone() is not None

    def path_caricati_da(self, pratica_numero: int, utente_id: int) -> set[str]:
        """Tutti i path di una pratica caricati da un utente specifico — usato
        per marcare in lista quali file quell'utente può eliminare."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT path FROM pratica_file_uploader "
                "WHERE pratica_numero = ? AND caricato_da = ?",
                (pratica_numero, utente_id),
            )
            return {row["path"] for row in cur.fetchall()}

    def rimuovi(self, path: Path) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM pratica_file_uploader WHERE path = ?", (str(path),))
