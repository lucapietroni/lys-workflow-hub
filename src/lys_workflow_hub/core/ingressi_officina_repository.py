"""Ingressi officina: bozza di pratica creata da un operatore prima che la
pratica esista in WinCar (ruolo "operatore", `web/routes_operatore.py`).

Non è un satellite di una pratica WinCar esistente (a differenza di
`pratica_note`/`pratica_eventi`/`pratica_assegnazioni`, che referenziano
un `numero_pratica` già presente in WinCar): un ingresso vive
autonomamente finché un admin non lo "collega" a un numero pratica creato
a mano in WinCar (`web/routes_ingressi.py`), momento in cui
`numero_pratica_wincar` viene valorizzato e i file spostati nella
cartella WinCar reale (vedi `core/pratica_files.py:save_upload`).

Nome deliberatamente diverso da "bozza" (`draft_repository.py` usa già
quel nome per le bozze di risposta email alle compagnie, M4 — dominio
non correlato).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

TipoFile = Literal["cid", "documento_identita", "libretto", "foto_danno"]
TIPI_FILE = ("cid", "documento_identita", "libretto", "foto_danno")

TIPO_FILE_LABELS = {
    "cid": "CID (constatazione amichevole)",
    "documento_identita": "Documento d'identità",
    "libretto": "Libretto",
    "foto_danno": "Foto danno",
}

STATO_IN_ATTESA = "in_attesa"
STATO_COLLEGATO = "collegato"
STATO_ANNULLATO = "annullato"
STATI = (STATO_IN_ATTESA, STATO_COLLEGATO, STATO_ANNULLATO)


@dataclass(frozen=True)
class IngressoFile:
    id: int
    ingresso_id: int
    tipo: str
    nome_file: str
    nome_file_originale: str
    caricato_il: datetime | None

    @property
    def tipo_label(self) -> str:
        return TIPO_FILE_LABELS.get(self.tipo, self.tipo)

    @property
    def categoria_upload(self) -> str:
        """Mappa il tipo documento sulla categoria attesa da
        `pratica_files.save_upload` (solo `"foto"` o `"documento"`)."""
        return "foto" if self.tipo == "foto_danno" else "documento"


@dataclass(frozen=True)
class Ingresso:
    id: int
    cliente_nominativo: str
    targa: str
    note: str
    stato: str
    creato_da: int
    creato_il: datetime | None
    numero_pratica_wincar: int | None
    collegato_da: int | None
    collegato_il: datetime | None
    file: tuple[IngressoFile, ...] = field(default_factory=tuple)

    @property
    def is_in_attesa(self) -> bool:
        return self.stato == STATO_IN_ATTESA


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingressi_officina (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_nominativo     TEXT NOT NULL,
    targa                  TEXT NOT NULL DEFAULT '',
    note                   TEXT NOT NULL DEFAULT '',
    stato                  TEXT NOT NULL DEFAULT 'in_attesa',
    creato_da              INTEGER NOT NULL,
    creato_il              TEXT NOT NULL,
    numero_pratica_wincar  INTEGER,
    collegato_da           INTEGER,
    collegato_il           TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingressi_officina_stato
    ON ingressi_officina(stato);

CREATE TABLE IF NOT EXISTS ingressi_officina_file (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ingresso_id           INTEGER NOT NULL REFERENCES ingressi_officina(id),
    tipo                  TEXT NOT NULL,
    nome_file             TEXT NOT NULL,
    nome_file_originale   TEXT NOT NULL,
    caricato_il           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ingressi_officina_file_ingresso
    ON ingressi_officina_file(ingresso_id);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class IngressiOfficinaRepository:
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
    def _row_to_file(row: sqlite3.Row) -> IngressoFile:
        d = dict(row)
        return IngressoFile(
            id=d["id"],
            ingresso_id=d["ingresso_id"],
            tipo=d["tipo"],
            nome_file=d["nome_file"],
            nome_file_originale=d["nome_file_originale"],
            caricato_il=_parse_dt(d.get("caricato_il")),
        )

    def _row_to_ingresso(self, row: sqlite3.Row, file: tuple[IngressoFile, ...]) -> Ingresso:
        d = dict(row)
        return Ingresso(
            id=d["id"],
            cliente_nominativo=d["cliente_nominativo"],
            targa=d["targa"],
            note=d["note"],
            stato=d["stato"],
            creato_da=d["creato_da"],
            creato_il=_parse_dt(d.get("creato_il")),
            numero_pratica_wincar=d.get("numero_pratica_wincar"),
            collegato_da=d.get("collegato_da"),
            collegato_il=_parse_dt(d.get("collegato_il")),
            file=file,
        )

    def crea(self, *, cliente_nominativo: str, targa: str, note: str, creato_da: int) -> Ingresso:
        cliente_nominativo = cliente_nominativo.strip()
        if not cliente_nominativo:
            raise ValueError("Il nominativo del cliente è obbligatorio.")
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO ingressi_officina "
                "(cliente_nominativo, targa, note, stato, creato_da, creato_il) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cliente_nominativo, targa.strip(), note.strip(), STATO_IN_ATTESA, int(creato_da), now),
            )
            row = conn.execute(
                "SELECT * FROM ingressi_officina WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return self._row_to_ingresso(row, ())

    def get(self, ingresso_id: int) -> Ingresso | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ingressi_officina WHERE id = ?", (int(ingresso_id),)
            ).fetchone()
            if row is None:
                return None
            file_rows = conn.execute(
                "SELECT * FROM ingressi_officina_file WHERE ingresso_id = ? ORDER BY caricato_il",
                (int(ingresso_id),),
            ).fetchall()
        return self._row_to_ingresso(row, tuple(self._row_to_file(r) for r in file_rows))

    def list_per_stato(self, stato: str) -> list[Ingresso]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ingressi_officina WHERE stato = ? ORDER BY creato_il DESC",
                (stato,),
            ).fetchall()
            ingressi = []
            for row in rows:
                file_rows = conn.execute(
                    "SELECT * FROM ingressi_officina_file WHERE ingresso_id = ? ORDER BY caricato_il",
                    (row["id"],),
                ).fetchall()
                ingressi.append(self._row_to_ingresso(row, tuple(self._row_to_file(r) for r in file_rows)))
        return ingressi

    def count_in_attesa(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ingressi_officina WHERE stato = ?",
                (STATO_IN_ATTESA,),
            ).fetchone()
        return int(row["n"])

    def aggiungi_file(
        self, ingresso_id: int, *, tipo: str, nome_file: str, nome_file_originale: str
    ) -> IngressoFile:
        if tipo not in TIPI_FILE:
            raise ValueError(f"Tipo file non valido: {tipo!r}")
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO ingressi_officina_file "
                "(ingresso_id, tipo, nome_file, nome_file_originale, caricato_il) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(ingresso_id), tipo, nome_file, nome_file_originale, now),
            )
            row = conn.execute(
                "SELECT * FROM ingressi_officina_file WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return self._row_to_file(row)

    def elimina_file(self, file_id: int, ingresso_id: int) -> IngressoFile | None:
        """`ingresso_id` obbligatorio nel WHERE per lo stesso motivo IDOR di
        `PraticaNoteRepository.delete`: senza, si potrebbe eliminare il file
        di un altro ingresso indovinando/incrementando l'id. Ritorna il
        record eliminato (serve al chiamante per cancellare il file fisico
        di staging), `None` se non trovato."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ingressi_officina_file WHERE id = ? AND ingresso_id = ?",
                (int(file_id), int(ingresso_id)),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM ingressi_officina_file WHERE id = ?", (int(file_id),))
        return self._row_to_file(row)

    def collega(self, ingresso_id: int, *, numero_pratica_wincar: int, collegato_da: int) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE ingressi_officina SET stato = ?, numero_pratica_wincar = ?, "
                "collegato_da = ?, collegato_il = ? WHERE id = ? AND stato = ?",
                (STATO_COLLEGATO, int(numero_pratica_wincar), int(collegato_da), now,
                 int(ingresso_id), STATO_IN_ATTESA),
            )
            return cur.rowcount > 0

    def annulla(self, ingresso_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE ingressi_officina SET stato = ? WHERE id = ? AND stato = ?",
                (STATO_ANNULLATO, int(ingresso_id), STATO_IN_ATTESA),
            )
            return cur.rowcount > 0
