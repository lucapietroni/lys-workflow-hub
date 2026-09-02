"""Fatture (attive/passive) e loro ripartizione sulle pratiche (Fase 1: schema).

Storage SQLite locale (`data/lys_hub.db`):
  - ``contabilita_fattura``          — una riga per fattura elettronica
  - ``contabilita_fattura_pratica``  — tabella ponte fattura ↔ pratica, che
    supporta sia il collegamento 1:1 sia lo split di una fattura su più
    pratiche (``importo_assegnato`` per riga).

NON è un registro IVA: ``contabilita_fattura`` è uno specchio di comodo delle
fatture che transitano da/verso lo SDI. ``imponibile`` / ``importo_iva`` /
``importo_totale`` sono complessivi (nessun dettaglio per aliquota) e servono
solo a generare i movimenti e la reportistica gestionale.

La UI di gestione fatture arriva nella Fase 3 (integrazione SDI); qui c'è il
solo modello dati + le operazioni di base, riusate dal resto del modulo.

Idempotenza (per il polling SDI della Fase 3):
  - ``UNIQUE(sdi_id)`` quando valorizzato (id messaggio del provider);
  - ``UNIQUE(tipo, numero, anno, controparte_piva)`` come chiave naturale.
``create`` è idempotente: se la fattura esiste già, restituisce quella
esistente senza modificarla.
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


TIPO_ATTIVA = "attiva"
TIPO_PASSIVA = "passiva"
TIPI = (TIPO_ATTIVA, TIPO_PASSIVA)

TIPO_LABELS = {
    TIPO_ATTIVA: "Attiva (emessa)",
    TIPO_PASSIVA: "Passiva (ricevuta)",
}

ORIGINE_MANUALE = "manuale"
ORIGINE_SDI = "sdi"
ORIGINI = (ORIGINE_MANUALE, ORIGINE_SDI)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contabilita_fattura (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo              TEXT    NOT NULL,
    numero            TEXT    NOT NULL,
    anno              INTEGER NOT NULL,
    data              TEXT    NOT NULL,
    controparte_nome  TEXT    NOT NULL DEFAULT '',
    controparte_piva  TEXT    NOT NULL DEFAULT '',
    imponibile        REAL    NOT NULL DEFAULT 0,
    importo_iva       REAL    NOT NULL DEFAULT 0,
    importo_totale    REAL    NOT NULL DEFAULT 0,
    stato_sdi         TEXT    NOT NULL DEFAULT '',
    xml_path          TEXT    NOT NULL DEFAULT '',
    pdf_path          TEXT    NOT NULL DEFAULT '',
    sdi_id            TEXT    NOT NULL DEFAULT '',
    origine           TEXT    NOT NULL DEFAULT 'manuale',
    created_at        TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_contabilita_fattura_naturale
    ON contabilita_fattura(tipo, numero, anno, controparte_piva);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contabilita_fattura_sdi_id
    ON contabilita_fattura(sdi_id) WHERE sdi_id <> '';
CREATE INDEX IF NOT EXISTS idx_contabilita_fattura_tipo
    ON contabilita_fattura(tipo);
CREATE INDEX IF NOT EXISTS idx_contabilita_fattura_anno
    ON contabilita_fattura(anno);

CREATE TABLE IF NOT EXISTS contabilita_fattura_pratica (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fattura_id        INTEGER NOT NULL,
    pratica_id        INTEGER NOT NULL,
    importo_assegnato REAL    NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_contabilita_fattura_pratica
    ON contabilita_fattura_pratica(fattura_id, pratica_id);
CREATE INDEX IF NOT EXISTS idx_contabilita_fattura_pratica_pratica
    ON contabilita_fattura_pratica(pratica_id);
CREATE INDEX IF NOT EXISTS idx_contabilita_fattura_pratica_fattura
    ON contabilita_fattura_pratica(fattura_id);
"""


@dataclass(frozen=True)
class Fattura:
    id: int | None
    tipo: str
    numero: str
    anno: int
    data: date
    controparte_nome: str = ""
    controparte_piva: str = ""
    imponibile: float = 0.0
    importo_iva: float = 0.0
    importo_totale: float = 0.0
    stato_sdi: str = ""
    xml_path: str = ""
    pdf_path: str = ""
    sdi_id: str = ""
    origine: str = ORIGINE_MANUALE
    created_at: datetime | None = None

    @property
    def tipo_label(self) -> str:
        return TIPO_LABELS.get(self.tipo, self.tipo)


@dataclass(frozen=True)
class FatturaPratica:
    id: int | None
    fattura_id: int
    pratica_id: int
    importo_assegnato: float = 0.0
    created_at: datetime | None = None


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


def _f(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return round(float(str(value).replace(",", ".")), 2)
    except ValueError as exc:
        raise ValueError(f"Importo non valido: {value!r}.") from exc


# --------------------------------------------------------------------------- #
#  Repository
# --------------------------------------------------------------------------- #


class ContabilitaFatturaRepository:
    """CRUD per ``contabilita_fattura`` + gestione della tabella ponte."""

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
    def _row_to_fattura(row: sqlite3.Row) -> Fattura:
        return Fattura(
            id=row["id"],
            tipo=row["tipo"],
            numero=row["numero"],
            anno=int(row["anno"]),
            data=_parse_date(row["data"]),
            controparte_nome=row["controparte_nome"] or "",
            controparte_piva=row["controparte_piva"] or "",
            imponibile=float(row["imponibile"]),
            importo_iva=float(row["importo_iva"]),
            importo_totale=float(row["importo_totale"]),
            stato_sdi=row["stato_sdi"] or "",
            xml_path=row["xml_path"] or "",
            pdf_path=row["pdf_path"] or "",
            sdi_id=row["sdi_id"] or "",
            origine=row["origine"] or ORIGINE_MANUALE,
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_fp(row: sqlite3.Row) -> FatturaPratica:
        return FatturaPratica(
            id=row["id"],
            fattura_id=int(row["fattura_id"]),
            pratica_id=int(row["pratica_id"]),
            importo_assegnato=float(row["importo_assegnato"]),
            created_at=_parse_dt(row["created_at"]),
        )

    # ---------------------------------------------------------------- create -

    def create(
        self,
        *,
        tipo: str,
        numero: str,
        anno: int,
        data: Any,
        controparte_nome: str = "",
        controparte_piva: str = "",
        imponibile: Any = 0,
        importo_iva: Any = 0,
        importo_totale: Any = 0,
        stato_sdi: str = "",
        xml_path: str = "",
        pdf_path: str = "",
        sdi_id: str = "",
        origine: str = ORIGINE_MANUALE,
    ) -> Fattura:
        if tipo not in TIPI:
            raise ValueError(f"Tipo fattura non valido: {tipo!r}.")
        if origine not in ORIGINI:
            raise ValueError(f"Origine non valida: {origine!r}.")
        numero = (numero or "").strip()
        if not numero:
            raise ValueError("Il numero della fattura è obbligatorio.")
        d = _parse_date(data)
        piva = (controparte_piva or "").strip()

        existing = self._find_duplicato(
            tipo=tipo, numero=numero, anno=int(anno), piva=piva, sdi_id=(sdi_id or "").strip()
        )
        if existing is not None:
            return existing

        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO contabilita_fattura "
                "(tipo, numero, anno, data, controparte_nome, controparte_piva, "
                " imponibile, importo_iva, importo_totale, stato_sdi, xml_path, "
                " pdf_path, sdi_id, origine, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tipo,
                    numero,
                    int(anno),
                    d.isoformat(),
                    (controparte_nome or "").strip(),
                    piva,
                    _f(imponibile),
                    _f(importo_iva),
                    _f(importo_totale),
                    (stato_sdi or "").strip(),
                    (xml_path or "").strip(),
                    (pdf_path or "").strip(),
                    (sdi_id or "").strip(),
                    origine,
                    _iso_now(),
                ),
            )
            new_id = cur.lastrowid
        return self.get(int(new_id))  # type: ignore[return-value]

    def _find_duplicato(
        self, *, tipo: str, numero: str, anno: int, piva: str, sdi_id: str
    ) -> Fattura | None:
        with self._connect() as conn:
            if sdi_id:
                row = conn.execute(
                    "SELECT * FROM contabilita_fattura WHERE sdi_id = ?",
                    (sdi_id,),
                ).fetchone()
                if row:
                    return self._row_to_fattura(row)
            row = conn.execute(
                "SELECT * FROM contabilita_fattura "
                "WHERE tipo = ? AND numero = ? AND anno = ? AND controparte_piva = ?",
                (tipo, numero, int(anno), piva),
            ).fetchone()
        return self._row_to_fattura(row) if row else None

    def find(
        self,
        *,
        tipo: str,
        numero: str = "",
        anno: int = 0,
        controparte_piva: str = "",
        sdi_id: str = "",
    ) -> Fattura | None:
        """Cerca una fattura per sdi_id o per chiave naturale. None se assente."""
        return self._find_duplicato(
            tipo=tipo,
            numero=(numero or "").strip(),
            anno=int(anno),
            piva=(controparte_piva or "").strip(),
            sdi_id=(sdi_id or "").strip(),
        )

    def aggiorna_stato_sdi(
        self, fattura_id: int, *, stato_sdi: str, sdi_id: str = ""
    ) -> Fattura | None:
        with self._connect() as conn:
            if sdi_id:
                conn.execute(
                    "UPDATE contabilita_fattura SET stato_sdi = ?, sdi_id = ? WHERE id = ?",
                    ((stato_sdi or "").strip(), sdi_id.strip(), int(fattura_id)),
                )
            else:
                conn.execute(
                    "UPDATE contabilita_fattura SET stato_sdi = ? WHERE id = ?",
                    ((stato_sdi or "").strip(), int(fattura_id)),
                )
        return self.get(fattura_id)

    def aggiorna_pdf_path(self, fattura_id: int, pdf_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE contabilita_fattura SET pdf_path = ? WHERE id = ?",
                ((pdf_path or "").strip(), int(fattura_id)),
            )

    # ----------------------------------------------------------------- query -

    def get(self, fattura_id: int) -> Fattura | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM contabilita_fattura WHERE id = ?",
                (int(fattura_id),),
            ).fetchone()
        return self._row_to_fattura(row) if row else None

    def list(
        self,
        *,
        tipo: str | None = None,
        anno: int | None = None,
        limit: int = 500,
    ) -> list[Fattura]:
        clauses: list[str] = []
        params: list[Any] = []
        if tipo:
            clauses.append("tipo = ?")
            params.append(tipo)
        if anno:
            clauses.append("anno = ?")
            params.append(int(anno))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contabilita_fattura"
                + where
                + " ORDER BY data DESC, id DESC LIMIT ?",
                [*params, int(limit)],
            ).fetchall()
        return [self._row_to_fattura(r) for r in rows]

    def list_non_collegate(self, *, tipo: str = TIPO_PASSIVA) -> list[Fattura]:
        """Fatture senza alcuna riga in ``contabilita_fattura_pratica``.

        Alimenta la coda "fatture passive da smistare" della Fase 4.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT f.* FROM contabilita_fattura f "
                "LEFT JOIN contabilita_fattura_pratica fp ON fp.fattura_id = f.id "
                "WHERE f.tipo = ? AND fp.id IS NULL "
                "ORDER BY f.data DESC, f.id DESC",
                (tipo,),
            ).fetchall()
        return [self._row_to_fattura(r) for r in rows]

    def delete(self, fattura_id: int) -> bool:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM contabilita_fattura_pratica WHERE fattura_id = ?",
                (int(fattura_id),),
            )
            cur = conn.execute(
                "DELETE FROM contabilita_fattura WHERE id = ?",
                (int(fattura_id),),
            )
            return cur.rowcount > 0

    # ---------------------------------------------------------- tabella ponte -

    def link_pratica(
        self, fattura_id: int, pratica_id: int, *, importo_assegnato: Any = 0
    ) -> FatturaPratica:
        """Collega (o aggiorna il collegamento di) una fattura a una pratica."""
        if self.get(fattura_id) is None:
            raise ValueError(f"Fattura id={fattura_id} non trovata.")
        imp = _f(importo_assegnato)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO contabilita_fattura_pratica "
                "(fattura_id, pratica_id, importo_assegnato, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(fattura_id, pratica_id) "
                "DO UPDATE SET importo_assegnato = excluded.importo_assegnato",
                (int(fattura_id), int(pratica_id), imp, _iso_now()),
            )
            row = conn.execute(
                "SELECT * FROM contabilita_fattura_pratica "
                "WHERE fattura_id = ? AND pratica_id = ?",
                (int(fattura_id), int(pratica_id)),
            ).fetchone()
        return self._row_to_fp(row)

    def unlink_pratica(self, fattura_id: int, pratica_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM contabilita_fattura_pratica "
                "WHERE fattura_id = ? AND pratica_id = ?",
                (int(fattura_id), int(pratica_id)),
            )
            return cur.rowcount > 0

    def list_pratiche(self, fattura_id: int) -> list[FatturaPratica]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contabilita_fattura_pratica "
                "WHERE fattura_id = ? ORDER BY id",
                (int(fattura_id),),
            ).fetchall()
        return [self._row_to_fp(r) for r in rows]

    def list_fatture_per_pratica(self, pratica_id: int) -> list[tuple[Fattura, float]]:
        """(fattura, importo_assegnato) per ogni fattura collegata alla pratica.

        Usato dalla scheda economica della pratica (Fase 2).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT f.*, fp.importo_assegnato AS _assegnato "
                "FROM contabilita_fattura_pratica fp "
                "JOIN contabilita_fattura f ON f.id = fp.fattura_id "
                "WHERE fp.pratica_id = ? "
                "ORDER BY f.data DESC, f.id DESC",
                (int(pratica_id),),
            ).fetchall()
        return [(self._row_to_fattura(r), float(r["_assegnato"])) for r in rows]
