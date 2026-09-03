"""Anagrafica delle categorie di contabilità gestionale (Fase 1).

Storage SQLite locale (`data/lys_hub.db`), tabella ``contabilita_categoria``.

NON è contabilità fiscale: nessuna partita doppia, nessun piano dei conti
ufficiale. Una categoria è solo un'etichetta per classificare un movimento
come ricavo o costo, così da poter leggere il margine per pratica e la
ripartizione delle spese per tipo.

Al primo avvio (tabella vuota) vengono inserite alcune categorie di
partenza tipiche di una carrozzeria — modificabili/disattivabili da UI.
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


TIPO_RICAVO = "ricavo"
TIPO_COSTO = "costo"
TIPI = (TIPO_RICAVO, TIPO_COSTO)

TIPO_LABELS = {
    TIPO_RICAVO: "Ricavo",
    TIPO_COSTO: "Costo",
}

# Nome della categoria per le note di credito (TipoDocumento TD04/TD08/TD24
# nell'XML). Il segno lo porta il movimento (uscita per una NC attiva =
# storno di ricavo, entrata per una NC passiva = storno di costo).
CATEGORIA_NOTA_CREDITO = "Nota di credito"
_CATEGORIA_NC_VECCHIO_NOME = "Note di credito"  # rinominata in v4.26.2

# Categorie inserite al primo avvio (tabella vuota). (nome, tipo).
_SEED_CATEGORIE: tuple[tuple[str, str], ...] = (
    ("Riparazioni carrozzeria", TIPO_RICAVO),
    ("Rivalse e franchigie", TIPO_RICAVO),
    (CATEGORIA_NOTA_CREDITO, TIPO_COSTO),
    ("Ricambi", TIPO_COSTO),
    ("Manodopera", TIPO_COSTO),
    ("Verniciatura", TIPO_COSTO),
    ("Auto cortesia", TIPO_COSTO),
    ("Assicurazioni aziendali", TIPO_COSTO),
    ("Utenze", TIPO_COSTO),
    ("Affitto", TIPO_COSTO),
    ("Consulenze", TIPO_COSTO),
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contabilita_categoria (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT    NOT NULL,
    tipo        TEXT    NOT NULL,
    attiva      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_contabilita_categoria_nome
    ON contabilita_categoria(nome COLLATE NOCASE);
"""


@dataclass(frozen=True)
class Categoria:
    id: int | None
    nome: str
    tipo: str
    attiva: bool = True
    created_at: datetime | None = None

    @property
    def tipo_label(self) -> str:
        return TIPO_LABELS.get(self.tipo, self.tipo)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ContabilitaCategoriaRepository:
    """CRUD per ``contabilita_categoria``."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM contabilita_categoria"
            ).fetchone()["n"]
            now = _iso_now()
            if not n:
                # OR IGNORE: se due processi inizializzano un DB nuovo in
                # parallelo, il secondo non deve sollevare IntegrityError qui.
                conn.executemany(
                    "INSERT OR IGNORE INTO contabilita_categoria "
                    "(nome, tipo, attiva, created_at) VALUES (?, ?, 1, ?)",
                    [(nome, tipo, now) for nome, tipo in _SEED_CATEGORIE],
                )
                logger.info(
                    "contabilita_categoria: inserite %d categorie di partenza",
                    len(_SEED_CATEGORIE),
                )
            else:
                # Migrazioni categorie su DB già popolati.
                # 1) rinomina "Note di credito" -> "Nota di credito" (v4.26.2)
                conn.execute(
                    "UPDATE contabilita_categoria SET nome = ? "
                    "WHERE nome = ? AND NOT EXISTS "
                    "(SELECT 1 FROM contabilita_categoria WHERE nome = ?)",
                    (CATEGORIA_NOTA_CREDITO, _CATEGORIA_NC_VECCHIO_NOME, CATEGORIA_NOTA_CREDITO),
                )
                # 2) garantisci la categoria nota di credito.
                conn.execute(
                    "INSERT OR IGNORE INTO contabilita_categoria "
                    "(nome, tipo, attiva, created_at) VALUES (?, ?, 1, ?)",
                    (CATEGORIA_NOTA_CREDITO, TIPO_COSTO, now),
                )

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
    def _row_to_categoria(row: sqlite3.Row) -> Categoria:
        return Categoria(
            id=row["id"],
            nome=row["nome"],
            tipo=row["tipo"],
            attiva=bool(row["attiva"]),
            created_at=_parse_dt(row["created_at"]),
        )

    # ------------------------------------------------------------------ query -

    def list_all(self, *, solo_attive: bool = False) -> list[Categoria]:
        sql = "SELECT * FROM contabilita_categoria"
        if solo_attive:
            sql += " WHERE attiva = 1"
        sql += " ORDER BY tipo, nome COLLATE NOCASE"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row_to_categoria(r) for r in rows]

    def get(self, categoria_id: int) -> Categoria | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM contabilita_categoria WHERE id = ?",
                (int(categoria_id),),
            ).fetchone()
        return self._row_to_categoria(row) if row else None

    # ----------------------------------------------------------------- mutate -

    def create(self, *, nome: str, tipo: str) -> Categoria:
        nome = (nome or "").strip()
        if not nome:
            raise ValueError("Il nome della categoria è obbligatorio.")
        if tipo not in TIPI:
            raise ValueError(f"Tipo non valido: {tipo!r} (atteso 'ricavo' o 'costo').")
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO contabilita_categoria (nome, tipo, attiva, created_at) "
                    "VALUES (?, ?, 1, ?)",
                    (nome, tipo, _iso_now()),
                )
                new_id = cur.lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Esiste già una categoria '{nome}'.") from exc
        return self.get(int(new_id))  # type: ignore[return-value]

    def update(
        self, categoria_id: int, *, nome: str, tipo: str, attiva: bool
    ) -> Categoria:
        existing = self.get(categoria_id)
        if existing is None:
            raise ValueError(f"Categoria id={categoria_id} non trovata.")
        nome = (nome or "").strip()
        if not nome:
            raise ValueError("Il nome della categoria è obbligatorio.")
        if tipo not in TIPI:
            raise ValueError(f"Tipo non valido: {tipo!r} (atteso 'ricavo' o 'costo').")
        with self._connect() as conn:
            try:
                conn.execute(
                    "UPDATE contabilita_categoria SET nome = ?, tipo = ?, attiva = ? "
                    "WHERE id = ?",
                    (nome, tipo, 1 if attiva else 0, int(categoria_id)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"Esiste già un'altra categoria '{nome}'."
                ) from exc
        return self.get(categoria_id)  # type: ignore[return-value]

    def set_attiva(self, categoria_id: int, attiva: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE contabilita_categoria SET attiva = ? WHERE id = ?",
                (1 if attiva else 0, int(categoria_id)),
            )

    def delete(self, categoria_id: int) -> bool:
        """Elimina la categoria SOLO se nessun movimento la usa.

        Se è referenziata restituisce ``False`` senza toccare nulla: il
        chiamante dovrebbe disattivarla (``set_attiva(id, False)``) invece
        di cancellarla, così lo storico dei movimenti resta leggibile.
        """
        with self._connect() as conn:
            try:
                ref = conn.execute(
                    "SELECT COUNT(*) AS n FROM contabilita_movimento WHERE categoria_id = ?",
                    (int(categoria_id),),
                ).fetchone()["n"]
            except sqlite3.OperationalError:
                # Tabella movimenti non ancora creata in questo DB: nessun
                # riferimento possibile.
                ref = 0
            if ref:
                return False
            cur = conn.execute(
                "DELETE FROM contabilita_categoria WHERE id = ?",
                (int(categoria_id),),
            )
            return cur.rowcount > 0
