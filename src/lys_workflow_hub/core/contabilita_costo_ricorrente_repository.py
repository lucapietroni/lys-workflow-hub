"""Costi ricorrenti non fatturati (Fase 5): affitto, autolavaggi, ecc.

Un "costo ricorrente" è un template: categoria + importo + cadenza. Un job
giornaliero (:mod:`lys_workflow_hub.workflows.contabilita.ricorrenti`) genera i
``contabilita_movimento`` mancanti per i periodi scaduti, a partire da
``data_inizio``. Il watermark ``ultimo_periodo`` garantisce che ogni periodo
sia generato una sola volta: se l'operatore elimina il movimento generato,
NON viene ricreato.

Storage SQLite locale (`data/lys_hub.db`), tabella
``contabilita_costo_ricorrente``.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator


logger = logging.getLogger(__name__)


CADENZA_MENSILE = "mensile"
CADENZA_BIMESTRALE = "bimestrale"
CADENZA_TRIMESTRALE = "trimestrale"
CADENZA_ANNUALE = "annuale"
CADENZE = (CADENZA_MENSILE, CADENZA_BIMESTRALE, CADENZA_TRIMESTRALE, CADENZA_ANNUALE)

CADENZA_MESI = {
    CADENZA_MENSILE: 1,
    CADENZA_BIMESTRALE: 2,
    CADENZA_TRIMESTRALE: 3,
    CADENZA_ANNUALE: 12,
}
CADENZA_LABELS = {
    CADENZA_MENSILE: "Mensile",
    CADENZA_BIMESTRALE: "Bimestrale",
    CADENZA_TRIMESTRALE: "Trimestrale",
    CADENZA_ANNUALE: "Annuale",
}


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contabilita_costo_ricorrente (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nome           TEXT    NOT NULL,
    categoria_id   INTEGER,
    importo        REAL    NOT NULL,
    importo_iva    REAL,
    cadenza        TEXT    NOT NULL,
    giorno_mese    INTEGER NOT NULL DEFAULT 1,
    descrizione    TEXT    NOT NULL DEFAULT '',
    data_inizio    TEXT    NOT NULL,
    attivo         INTEGER NOT NULL DEFAULT 1,
    ultimo_periodo TEXT,
    created_at     TEXT    NOT NULL
);
"""


@dataclass(frozen=True)
class CostoRicorrente:
    id: int | None
    nome: str
    categoria_id: int | None
    importo: float
    importo_iva: float | None
    cadenza: str
    giorno_mese: int
    descrizione: str
    data_inizio: date
    attivo: bool
    ultimo_periodo: date | None
    created_at: datetime | None = None

    @property
    def cadenza_label(self) -> str:
        return CADENZA_LABELS.get(self.cadenza, self.cadenza)

    @property
    def passo_mesi(self) -> int:
        return CADENZA_MESI.get(self.cadenza, 1)


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


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise ValueError(f"Data non valida: {value!r} (atteso AAAA-MM-GG).") from exc


def _opt_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return _parse_date(value)


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _coerce_importo(value: Any, *, campo: str, obbligatorio: bool) -> float | None:
    raw = str(value).strip() if value is not None else ""
    if not raw:
        if obbligatorio:
            raise ValueError(f"Il campo {campo} è obbligatorio.")
        return None
    try:
        num = round(float(raw.replace(",", ".")), 2)
    except ValueError as exc:
        raise ValueError(f"Il campo {campo} deve essere un numero.") from exc
    if num < 0:
        raise ValueError(f"Il campo {campo} non può essere negativo.")
    return num


def _giorno(value: Any) -> int:
    try:
        g = int(str(value).strip())
    except (TypeError, ValueError):
        g = 1
    return max(1, min(28, g))


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


class ContabilitaCostoRicorrenteRepository:
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
    def _row(row: sqlite3.Row) -> CostoRicorrente:
        return CostoRicorrente(
            id=row["id"],
            nome=row["nome"],
            categoria_id=row["categoria_id"],
            importo=float(row["importo"]),
            importo_iva=(float(row["importo_iva"]) if row["importo_iva"] is not None else None),
            cadenza=row["cadenza"],
            giorno_mese=int(row["giorno_mese"]),
            descrizione=row["descrizione"] or "",
            data_inizio=_parse_date(row["data_inizio"]),
            attivo=bool(row["attivo"]),
            ultimo_periodo=_opt_date(row["ultimo_periodo"]),
            created_at=_parse_dt(row["created_at"]),
        )

    # ------------------------------------------------------------------ query -

    def list_all(self, *, solo_attivi: bool = False) -> list[CostoRicorrente]:
        sql = "SELECT * FROM contabilita_costo_ricorrente"
        if solo_attivi:
            sql += " WHERE attivo = 1"
        sql += " ORDER BY nome COLLATE NOCASE"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row(r) for r in rows]

    def get(self, costo_id: int) -> CostoRicorrente | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM contabilita_costo_ricorrente WHERE id = ?",
                (int(costo_id),),
            ).fetchone()
        return self._row(row) if row else None

    # ----------------------------------------------------------------- mutate -

    def create(
        self,
        *,
        nome: str,
        categoria_id: int | None,
        importo: Any,
        cadenza: str,
        giorno_mese: Any,
        data_inizio: Any,
        descrizione: str = "",
        importo_iva: Any = None,
    ) -> CostoRicorrente:
        nome = (nome or "").strip()
        if not nome:
            raise ValueError("Il nome è obbligatorio.")
        if cadenza not in CADENZE:
            raise ValueError(f"Cadenza non valida: {cadenza!r}.")
        imp = _coerce_importo(importo, campo="importo", obbligatorio=True)
        iva = _coerce_importo(importo_iva, campo="IVA", obbligatorio=False)
        di = _parse_date(data_inizio)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO contabilita_costo_ricorrente "
                "(nome, categoria_id, importo, importo_iva, cadenza, giorno_mese, "
                " descrizione, data_inizio, attivo, ultimo_periodo, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?)",
                (
                    nome,
                    int(categoria_id) if categoria_id else None,
                    imp,
                    iva,
                    cadenza,
                    _giorno(giorno_mese),
                    (descrizione or "").strip(),
                    di.isoformat(),
                    _iso_now(),
                ),
            )
            new_id = cur.lastrowid
        return self.get(int(new_id))  # type: ignore[return-value]

    def update(
        self,
        costo_id: int,
        *,
        nome: str,
        categoria_id: int | None,
        importo: Any,
        cadenza: str,
        giorno_mese: Any,
        data_inizio: Any,
        descrizione: str = "",
        importo_iva: Any = None,
        attivo: bool = True,
    ) -> CostoRicorrente:
        if self.get(costo_id) is None:
            raise ValueError(f"Costo ricorrente id={costo_id} non trovato.")
        nome = (nome or "").strip()
        if not nome:
            raise ValueError("Il nome è obbligatorio.")
        if cadenza not in CADENZE:
            raise ValueError(f"Cadenza non valida: {cadenza!r}.")
        imp = _coerce_importo(importo, campo="importo", obbligatorio=True)
        iva = _coerce_importo(importo_iva, campo="IVA", obbligatorio=False)
        di = _parse_date(data_inizio)
        with self._connect() as conn:
            conn.execute(
                "UPDATE contabilita_costo_ricorrente SET "
                " nome = ?, categoria_id = ?, importo = ?, importo_iva = ?, "
                " cadenza = ?, giorno_mese = ?, descrizione = ?, data_inizio = ?, "
                " attivo = ? WHERE id = ?",
                (
                    nome,
                    int(categoria_id) if categoria_id else None,
                    imp,
                    iva,
                    cadenza,
                    _giorno(giorno_mese),
                    (descrizione or "").strip(),
                    di.isoformat(),
                    1 if attivo else 0,
                    int(costo_id),
                ),
            )
        return self.get(costo_id)  # type: ignore[return-value]

    def segna_periodo_generato(self, costo_id: int, periodo: date) -> None:
        """Avanza il watermark ``ultimo_periodo`` (solo in avanti)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE contabilita_costo_ricorrente SET ultimo_periodo = ? "
                "WHERE id = ? AND (ultimo_periodo IS NULL OR ultimo_periodo < ?)",
                (periodo.isoformat(), int(costo_id), periodo.isoformat()),
            )

    def delete(self, costo_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM contabilita_costo_ricorrente WHERE id = ?",
                (int(costo_id),),
            )
            return cur.rowcount > 0
