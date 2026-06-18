"""Anagrafica auto di cortesia LYS + storico verbali generati.

Tabelle in lys_hub.db:
  auto_cortesia   — parco auto di cortesia
  verbali_cortesia — record di ogni verbale generato (per ereditare km/danni)
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class AutoCortesia:
    id: int
    targa: str
    marca_modello: str
    telaio: str
    franchigia_rca: str
    franchigia_kasco: str
    franchigia_furto_incendio: str
    note: str


@dataclass
class VerbaleRecord:
    id: int
    tipo: str          # "uscita" or "rientro"
    auto_id: int
    pratica_numero: int | None
    km: str
    livello_carburante: str
    danni: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""
    data_ora: str = ""
    created_at: str = ""


class AutoCortesiaRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_tables()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_tables(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_cortesia (
                    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                    targa                      TEXT    UNIQUE NOT NULL,
                    marca_modello              TEXT    NOT NULL,
                    telaio                     TEXT    DEFAULT '',
                    franchigia_rca             TEXT    DEFAULT '',
                    franchigia_kasco           TEXT    DEFAULT '',
                    franchigia_furto_incendio  TEXT    DEFAULT '',
                    note                       TEXT    DEFAULT '',
                    created_at                 TEXT    DEFAULT (datetime('now'))
                )
            """)
            # Migrazione per DB esistenti
            for col in ("franchigia_rca", "franchigia_kasco", "franchigia_furto_incendio"):
                try:
                    conn.execute(f"ALTER TABLE auto_cortesia ADD COLUMN {col} TEXT DEFAULT ''")
                except Exception:
                    pass
            conn.execute("""
                CREATE TABLE IF NOT EXISTS verbali_cortesia (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo               TEXT    NOT NULL,
                    auto_id            INTEGER NOT NULL REFERENCES auto_cortesia(id) ON DELETE CASCADE,
                    pratica_numero     INTEGER,
                    km                 TEXT    DEFAULT '',
                    livello_carburante TEXT    DEFAULT '',
                    danni_json         TEXT    DEFAULT '[]',
                    note               TEXT    DEFAULT '',
                    data_ora           TEXT    DEFAULT '',
                    created_at         TEXT    DEFAULT (datetime('now'))
                )
            """)

    # ------------------------------------------------------------------
    # CRUD auto
    # ------------------------------------------------------------------

    _SELECT = (
        "SELECT id, targa, marca_modello, telaio, "
        "franchigia_rca, franchigia_kasco, franchigia_furto_incendio, note "
        "FROM auto_cortesia"
    )

    def list_auto(self) -> list[AutoCortesia]:
        with self._conn() as conn:
            rows = conn.execute(self._SELECT + " ORDER BY targa").fetchall()
        return [self._row_to_auto(r) for r in rows]

    def get_auto(self, auto_id: int) -> AutoCortesia | None:
        with self._conn() as conn:
            row = conn.execute(
                self._SELECT + " WHERE id = ?", (auto_id,)
            ).fetchone()
        return self._row_to_auto(row) if row else None

    def create_auto(
        self, *, targa: str, marca_modello: str, telaio: str = "",
        franchigia_rca: str = "", franchigia_kasco: str = "",
        franchigia_furto_incendio: str = "", note: str = ""
    ) -> AutoCortesia:
        targa = targa.upper().strip()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO auto_cortesia "
                "(targa, marca_modello, telaio, franchigia_rca, franchigia_kasco, "
                "franchigia_furto_incendio, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (targa, marca_modello.strip(), telaio.strip(),
                 franchigia_rca.strip(), franchigia_kasco.strip(),
                 franchigia_furto_incendio.strip(), note.strip()),
            )
            return AutoCortesia(
                id=cur.lastrowid,
                targa=targa,
                marca_modello=marca_modello.strip(),
                telaio=telaio.strip(),
                franchigia_rca=franchigia_rca.strip(),
                franchigia_kasco=franchigia_kasco.strip(),
                franchigia_furto_incendio=franchigia_furto_incendio.strip(),
                note=note.strip(),
            )

    def update_auto(
        self, auto_id: int, *, targa: str, marca_modello: str, telaio: str = "",
        franchigia_rca: str = "", franchigia_kasco: str = "",
        franchigia_furto_incendio: str = "", note: str = ""
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE auto_cortesia SET targa=?, marca_modello=?, telaio=?, "
                "franchigia_rca=?, franchigia_kasco=?, franchigia_furto_incendio=?, note=? "
                "WHERE id=?",
                (targa.upper().strip(), marca_modello.strip(), telaio.strip(),
                 franchigia_rca.strip(), franchigia_kasco.strip(),
                 franchigia_furto_incendio.strip(), note.strip(), auto_id),
            )

    def delete_auto(self, auto_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM auto_cortesia WHERE id=?", (auto_id,))

    # ------------------------------------------------------------------
    # Verbali record
    # ------------------------------------------------------------------

    def save_verbale(
        self, *,
        tipo: str,
        auto_id: int,
        pratica_numero: int | None,
        km: str,
        livello_carburante: str,
        danni: list[tuple[str, str]],
        note: str,
        data_ora: str,
    ) -> int:
        danni_json = json.dumps(danni, ensure_ascii=False)
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO verbali_cortesia
                   (tipo, auto_id, pratica_numero, km, livello_carburante,
                    danni_json, note, data_ora)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (tipo, auto_id, pratica_numero, km, livello_carburante,
                 danni_json, note, data_ora),
            )
            return cur.lastrowid

    def get_last_rientro(self, auto_id: int) -> VerbaleRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, tipo, auto_id, pratica_numero, km, livello_carburante,
                          danni_json, note, data_ora, created_at
                   FROM verbali_cortesia
                   WHERE auto_id=? AND tipo='rientro'
                   ORDER BY id DESC LIMIT 1""",
                (auto_id,),
            ).fetchone()
        return self._row_to_verbale(row) if row else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_auto(row: sqlite3.Row) -> AutoCortesia:
        return AutoCortesia(
            id=row["id"],
            targa=row["targa"],
            marca_modello=row["marca_modello"],
            telaio=row["telaio"] or "",
            franchigia_rca=row["franchigia_rca"] or "",
            franchigia_kasco=row["franchigia_kasco"] or "",
            franchigia_furto_incendio=row["franchigia_furto_incendio"] or "",
            note=row["note"] or "",
        )

    @staticmethod
    def _row_to_verbale(row: sqlite3.Row) -> VerbaleRecord:
        try:
            danni = json.loads(row["danni_json"] or "[]")
        except (ValueError, TypeError):
            danni = []
        return VerbaleRecord(
            id=row["id"],
            tipo=row["tipo"],
            auto_id=row["auto_id"],
            pratica_numero=row["pratica_numero"],
            km=row["km"] or "",
            livello_carburante=row["livello_carburante"] or "",
            danni=danni,
            note=row["note"] or "",
            data_ora=row["data_ora"] or "",
            created_at=row["created_at"] or "",
        )
