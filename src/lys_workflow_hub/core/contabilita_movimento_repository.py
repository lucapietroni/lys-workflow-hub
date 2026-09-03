"""Movimenti di contabilità gestionale (Fase 1).

Storage SQLite locale (`data/lys_hub.db`), tabella ``contabilita_movimento``.

Un movimento è una singola entrata o uscita, classificata da una categoria
(:mod:`contabilita_categoria_repository`) e — opzionalmente — collegata a una
pratica WinCar (`pratica_id` = ``F_NUMPRA``, intero sciolto, nessuna FK reale
come nel resto del progetto) e/o a una fattura
(:mod:`contabilita_fattura_repository`).

NON è contabilità fiscale: nessun vincolo dare/avere, nessuna quadratura.
L'IVA (`importo_iva`) è un dato puramente informativo.

`stato`:
  - ``confermato`` — inserito/validato da un umano, entra nei report.
  - ``proposto``   — generato in automatico da una fattura SDI, in attesa di
    conferma. Escluso dai report finché non confermato (Fasi 3-4).
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


TIPO_ENTRATA = "entrata"
TIPO_USCITA = "uscita"
TIPI = (TIPO_ENTRATA, TIPO_USCITA)

TIPO_LABELS = {
    TIPO_ENTRATA: "Entrata",
    TIPO_USCITA: "Uscita",
}

ORIGINE_MANUALE = "manuale"
ORIGINE_FATTURA_SDI = "da_fattura_sdi"
ORIGINI = (ORIGINE_MANUALE, ORIGINE_FATTURA_SDI)

STATO_PROPOSTO = "proposto"
STATO_CONFERMATO = "confermato"
STATI = (STATO_PROPOSTO, STATO_CONFERMATO)

STATO_LABELS = {
    STATO_PROPOSTO: "Proposto",
    STATO_CONFERMATO: "Confermato",
}


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contabilita_movimento (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    data         TEXT    NOT NULL,
    importo      REAL    NOT NULL,
    tipo         TEXT    NOT NULL,
    categoria_id INTEGER,
    pratica_id   INTEGER,
    fattura_id   INTEGER,
    descrizione  TEXT    NOT NULL DEFAULT '',
    origine      TEXT    NOT NULL DEFAULT 'manuale',
    stato        TEXT    NOT NULL DEFAULT 'confermato',
    importo_iva  REAL,
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contabilita_movimento_pratica
    ON contabilita_movimento(pratica_id);
CREATE INDEX IF NOT EXISTS idx_contabilita_movimento_categoria
    ON contabilita_movimento(categoria_id);
CREATE INDEX IF NOT EXISTS idx_contabilita_movimento_data
    ON contabilita_movimento(data);
CREATE INDEX IF NOT EXISTS idx_contabilita_movimento_fattura
    ON contabilita_movimento(fattura_id);
CREATE INDEX IF NOT EXISTS idx_contabilita_movimento_stato
    ON contabilita_movimento(stato);
"""


@dataclass(frozen=True)
class Movimento:
    id: int | None
    data: date
    importo: float
    tipo: str
    categoria_id: int | None = None
    pratica_id: int | None = None
    fattura_id: int | None = None
    descrizione: str = ""
    origine: str = ORIGINE_MANUALE
    stato: str = STATO_CONFERMATO
    importo_iva: float | None = None
    created_at: datetime | None = None

    @property
    def tipo_label(self) -> str:
        return TIPO_LABELS.get(self.tipo, self.tipo)

    @property
    def stato_label(self) -> str:
        return STATO_LABELS.get(self.stato, self.stato)

    @property
    def importo_con_segno(self) -> float:
        """+importo per le entrate, -importo per le uscite."""
        return self.importo if self.tipo == TIPO_ENTRATA else -self.importo


@dataclass(frozen=True)
class TotaliMovimenti:
    entrate: float = 0.0
    uscite: float = 0.0

    @property
    def saldo(self) -> float:
        return self.entrate - self.uscite


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
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(
            f"Data non valida: {value!r} (formato atteso AAAA-MM-GG)."
        ) from exc


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _coerce_importo(value: Any, *, campo: str = "importo", obbligatorio: bool = True) -> float | None:
    raw = str(value).strip() if value is not None else ""
    if not raw:
        if obbligatorio:
            raise ValueError(f"Il campo {campo} è obbligatorio.")
        return None
    try:
        num = float(raw.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"Il campo {campo} deve essere un numero.") from exc
    if num < 0:
        raise ValueError(f"Il campo {campo} non può essere negativo.")
    return round(num, 2)


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


class ContabilitaMovimentoRepository:
    """CRUD + aggregati base per ``contabilita_movimento``."""

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
    def _row_to_movimento(row: sqlite3.Row) -> Movimento:
        return Movimento(
            id=row["id"],
            data=_parse_date(row["data"]),
            importo=float(row["importo"]),
            tipo=row["tipo"],
            categoria_id=row["categoria_id"],
            pratica_id=row["pratica_id"],
            fattura_id=row["fattura_id"],
            descrizione=row["descrizione"] or "",
            origine=row["origine"] or ORIGINE_MANUALE,
            stato=row["stato"] or STATO_CONFERMATO,
            importo_iva=(
                float(row["importo_iva"]) if row["importo_iva"] is not None else None
            ),
            created_at=_parse_dt(row["created_at"]),
        )

    # ---------------------------------------------------------------- create -

    def create(
        self,
        *,
        data: Any,
        importo: Any,
        tipo: str,
        categoria_id: int | None = None,
        pratica_id: int | None = None,
        fattura_id: int | None = None,
        descrizione: str = "",
        origine: str = ORIGINE_MANUALE,
        stato: str = STATO_CONFERMATO,
        importo_iva: Any = None,
    ) -> Movimento:
        d = _parse_date(data)
        imp = _coerce_importo(importo, campo="importo", obbligatorio=True)
        iva = _coerce_importo(importo_iva, campo="IVA", obbligatorio=False)
        if tipo not in TIPI:
            raise ValueError(f"Tipo movimento non valido: {tipo!r}.")
        if origine not in ORIGINI:
            raise ValueError(f"Origine non valida: {origine!r}.")
        if stato not in STATI:
            raise ValueError(f"Stato non valido: {stato!r}.")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO contabilita_movimento "
                "(data, importo, tipo, categoria_id, pratica_id, fattura_id, "
                " descrizione, origine, stato, importo_iva, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    d.isoformat(),
                    imp,
                    tipo,
                    int(categoria_id) if categoria_id else None,
                    int(pratica_id) if pratica_id else None,
                    int(fattura_id) if fattura_id else None,
                    (descrizione or "").strip(),
                    origine,
                    stato,
                    iva,
                    _iso_now(),
                ),
            )
            new_id = cur.lastrowid
        return self.get(int(new_id))  # type: ignore[return-value]

    # ----------------------------------------------------------------- query -

    def get(self, movimento_id: int) -> Movimento | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM contabilita_movimento WHERE id = ?",
                (int(movimento_id),),
            ).fetchone()
        return self._row_to_movimento(row) if row else None

    def _where(
        self,
        *,
        categoria_id: int | None,
        pratica_id: int | None,
        fattura_id: int | None,
        tipo: str | None,
        stato: str | None,
        dal: Any,
        al: Any,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if categoria_id is not None:
            clauses.append("categoria_id = ?")
            params.append(int(categoria_id))
        if pratica_id is not None:
            clauses.append("pratica_id = ?")
            params.append(int(pratica_id))
        if fattura_id is not None:
            clauses.append("fattura_id = ?")
            params.append(int(fattura_id))
        if tipo:
            clauses.append("tipo = ?")
            params.append(tipo)
        if stato:
            clauses.append("stato = ?")
            params.append(stato)
        if dal:
            clauses.append("data >= ?")
            params.append(_parse_date(dal).isoformat())
        if al:
            clauses.append("data <= ?")
            params.append(_parse_date(al).isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def list(
        self,
        *,
        categoria_id: int | None = None,
        pratica_id: int | None = None,
        fattura_id: int | None = None,
        tipo: str | None = None,
        stato: str | None = None,
        dal: Any = None,
        al: Any = None,
        limit: int = 500,
    ) -> list[Movimento]:
        where, params = self._where(
            categoria_id=categoria_id,
            pratica_id=pratica_id,
            fattura_id=fattura_id,
            tipo=tipo,
            stato=stato,
            dal=dal,
            al=al,
        )
        sql = (
            "SELECT * FROM contabilita_movimento"
            + where
            + " ORDER BY data DESC, id DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, [*params, int(limit)]).fetchall()
        return [self._row_to_movimento(r) for r in rows]

    def totali(
        self,
        *,
        categoria_id: int | None = None,
        pratica_id: int | None = None,
        fattura_id: int | None = None,
        stato: str | None = None,
        dal: Any = None,
        al: Any = None,
    ) -> TotaliMovimenti:
        """Somma entrate e uscite sul sottoinsieme filtrato."""
        where, params = self._where(
            categoria_id=categoria_id,
            pratica_id=pratica_id,
            fattura_id=fattura_id,
            tipo=None,
            stato=stato,
            dal=dal,
            al=al,
        )
        sql = (
            "SELECT tipo, COALESCE(SUM(importo), 0) AS tot "
            "FROM contabilita_movimento" + where + " GROUP BY tipo"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        acc = {TIPO_ENTRATA: 0.0, TIPO_USCITA: 0.0}
        for r in rows:
            if r["tipo"] in acc:
                acc[r["tipo"]] = round(float(r["tot"]), 2)
        return TotaliMovimenti(entrate=acc[TIPO_ENTRATA], uscite=acc[TIPO_USCITA])

    def list_by_fattura(self, fattura_id: int) -> list[Movimento]:
        return self.list(fattura_id=fattura_id, limit=1000)

    def conta(
        self,
        *,
        categoria_id: int | None = None,
        pratica_id: int | None = None,
        fattura_id: int | None = None,
        tipo: str | None = None,
        stato: str | None = None,
        dal: Any = None,
        al: Any = None,
    ) -> int:
        where, params = self._where(
            categoria_id=categoria_id, pratica_id=pratica_id, fattura_id=fattura_id,
            tipo=tipo, stato=stato, dal=dal, al=al,
        )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM contabilita_movimento" + where, params
            ).fetchone()
        return int(row["n"])

    def fattura_ids_con_proposti(self) -> set[int]:
        """Id delle fatture che hanno almeno un movimento in stato 'proposto'.

        Alimenta la coda "fatture passive da smistare" (Fase 4)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT fattura_id FROM contabilita_movimento "
                "WHERE stato = ? AND fattura_id IS NOT NULL",
                (STATO_PROPOSTO,),
            ).fetchall()
        return {int(r["fattura_id"]) for r in rows}

    def riepilogo_per_categoria(
        self,
        *,
        stato: str | None = STATO_CONFERMATO,
        dal: Any = None,
        al: Any = None,
    ) -> list[dict[str, Any]]:
        """Entrate/uscite aggregate per categoria_id sul periodo.

        Ritorna righe ``{categoria_id, entrate, uscite}`` (categoria_id può
        essere None per i movimenti senza categoria). Per la dashboard
        costi/ricavi della Fase 4."""
        where, params = self._where(
            categoria_id=None, pratica_id=None, fattura_id=None,
            tipo=None, stato=stato, dal=dal, al=al,
        )
        sql = (
            "SELECT categoria_id, "
            " COALESCE(SUM(CASE WHEN tipo = 'entrata' THEN importo END), 0) AS entrate, "
            " COALESCE(SUM(CASE WHEN tipo = 'uscita'  THEN importo END), 0) AS uscite "
            "FROM contabilita_movimento" + where + " GROUP BY categoria_id"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "categoria_id": r["categoria_id"],
                "entrate": round(float(r["entrate"]), 2),
                "uscite": round(float(r["uscite"]), 2),
            }
            for r in rows
        ]

    def delete_by_fattura(self, fattura_id: int, *, solo_sdi: bool = True) -> int:
        """Elimina i movimenti legati a una fattura. Ritorna quanti eliminati.

        Con ``solo_sdi`` (default) tocca solo i movimenti generati
        automaticamente (``origine = 'da_fattura_sdi'``), sia proposti sia
        confermati — così un (ri)smistamento sostituisce i propri movimenti
        ma NON tocca quelli inseriti a mano dall'operatore sulla stessa
        fattura."""
        sql = "DELETE FROM contabilita_movimento WHERE fattura_id = ?"
        params: list[Any] = [int(fattura_id)]
        if solo_sdi:
            sql += " AND origine = ?"
            params.append(ORIGINE_FATTURA_SDI)
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount

    # ---------------------------------------------------------------- mutate -

    def update(
        self,
        movimento_id: int,
        *,
        data: Any,
        importo: Any,
        tipo: str,
        categoria_id: int | None = None,
        pratica_id: int | None = None,
        descrizione: str = "",
        importo_iva: Any = None,
        stato: str | None = None,
    ) -> Movimento:
        existing = self.get(movimento_id)
        if existing is None:
            raise ValueError(f"Movimento id={movimento_id} non trovato.")
        d = _parse_date(data)
        imp = _coerce_importo(importo, campo="importo", obbligatorio=True)
        iva = _coerce_importo(importo_iva, campo="IVA", obbligatorio=False)
        if tipo not in TIPI:
            raise ValueError(f"Tipo movimento non valido: {tipo!r}.")
        nuovo_stato = existing.stato if stato is None else stato
        if nuovo_stato not in STATI:
            raise ValueError(f"Stato non valido: {nuovo_stato!r}.")
        with self._connect() as conn:
            conn.execute(
                "UPDATE contabilita_movimento SET "
                " data = ?, importo = ?, tipo = ?, categoria_id = ?, pratica_id = ?, "
                " descrizione = ?, importo_iva = ?, stato = ? "
                "WHERE id = ?",
                (
                    d.isoformat(),
                    imp,
                    tipo,
                    int(categoria_id) if categoria_id else None,
                    int(pratica_id) if pratica_id else None,
                    (descrizione or "").strip(),
                    iva,
                    nuovo_stato,
                    int(movimento_id),
                ),
            )
        return self.get(movimento_id)  # type: ignore[return-value]

    def set_stato(self, movimento_id: int, stato: str) -> None:
        if stato not in STATI:
            raise ValueError(f"Stato non valido: {stato!r}.")
        with self._connect() as conn:
            conn.execute(
                "UPDATE contabilita_movimento SET stato = ? WHERE id = ?",
                (stato, int(movimento_id)),
            )

    def delete(self, movimento_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM contabilita_movimento WHERE id = ?",
                (int(movimento_id),),
            )
            return cur.rowcount > 0
