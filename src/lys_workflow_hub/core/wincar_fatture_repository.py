"""Lettura read-only del modulo fatture di WinCar (``wcFatture.mdb``).

L'XML FatturaPA generato da WinCar NON contiene il numero pratica: il legame
fattura → pratica vive solo qui, nella testata ``TESFAT``:

    TESFAT.F_NUMFAT   numero fattura (int)
    TESFAT.F_ALFFAT   sezionale / alfa (di norma vuoto)
    TESFAT.F_DATFAT   data fattura
    TESFAT.F_TIPDOC   'FI' = fattura, 'NC' = nota di credito
    TESFAT.F_NUMPRA   numero pratica WinCar (F_NUMPRA <= 0 = nessuna pratica)
    TESFAT.F_TOTFAT   totale documento

Stesso pattern di ``WinCarRepository``: ODBC in sola lettura, mai una scrittura.
Su piattaforme senza driver Access (dev su Linux/WSL) i metodi sollevano
``RuntimeError``: il chiamante deve tollerarlo e proseguire senza il legame.
"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import pyodbc
except ImportError:  # pragma: no cover
    pyodbc = None  # type: ignore[assignment]

from lys_workflow_hub.config import Settings, get_settings

logger = logging.getLogger(__name__)

DB_FILE_FATTURE = "wcFatture.mdb"


def numero_fattura_int(numero: str) -> int | None:
    """Estrae la parte numerica dal ``Numero`` del documento FatturaPA.

    WinCar tiene numero (int) e sezionale (F_ALFFAT) separati; l'XML li
    concatena a volte (es. ``40``, ``2026/40``, ``40/A``). Prendiamo il primo
    gruppo di cifre "lungo" — l'ultimo se ci sono più gruppi separati da /.
    """
    if numero is None:
        return None
    parts = re.findall(r"\d+", str(numero))
    if not parts:
        return None
    # "2026/40" -> 40 ; "40" -> 40 ; "40/1" -> 40 (primo se il secondo è corto)
    if len(parts) >= 2 and len(parts[0]) == 4 and parts[0].startswith("20"):
        return int(parts[1])
    return int(parts[0])


class WinCarFattureRepository:
    """Lettore read-only di ``wcFatture.mdb``."""

    def __init__(self, archivio_root: Path, odbc_driver: str) -> None:
        self.archivio_root = Path(archivio_root)
        self.odbc_driver = odbc_driver

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "WinCarFattureRepository":
        s = settings or get_settings()
        return cls(archivio_root=s.wincar_archivio, odbc_driver=s.wincar_odbc_driver)

    @property
    def db_path(self) -> Path:
        return self.archivio_root / DB_FILE_FATTURE

    def disponibile(self) -> bool:
        return pyodbc is not None and self.db_path.is_file()

    @contextmanager
    def _connect(self) -> Iterator["pyodbc.Connection"]:
        if pyodbc is None:
            raise RuntimeError("pyodbc non installato (serve Windows + driver Access).")
        conn = pyodbc.connect(
            f"DRIVER={{{self.odbc_driver}}};DBQ={self.db_path};ReadOnly=1;",
            autocommit=True,
        )
        for enc in ("cp1252", "latin1", "utf-8"):
            try:
                conn.setdecoding(pyodbc.SQL_CHAR, encoding=enc)
                conn.setdecoding(pyodbc.SQL_WCHAR, encoding=enc)
                conn.setencoding(encoding=enc)
                break
            except Exception:
                continue
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ query -

    def pratica_per_fattura(
        self,
        numero_fattura: int,
        anno: int,
        *,
        alfa: str = "",
    ) -> int | None:
        """Numero pratica WinCar per (numero, anno) fattura. None se assente,
        ambiguo, o se la fattura non è legata a una pratica (F_NUMPRA <= 0)."""
        sql = (
            "SELECT F_NUMPRA, F_TOTFAT FROM TESFAT "
            "WHERE F_NUMFAT = ? AND YEAR(F_DATFAT) = ?"
        )
        params: list[object] = [int(numero_fattura), int(anno)]
        if alfa:
            sql += " AND F_ALFFAT = ?"
            params.append(alfa)
        with self._connect() as conn:
            rows = conn.cursor().execute(sql, params).fetchall()
        praticas: list[int] = []
        for r in rows:
            try:
                n = int(r[0])
            except (TypeError, ValueError):
                continue
            if n > 0:
                praticas.append(n)
        praticas = sorted(set(praticas))
        if len(praticas) == 1:
            return praticas[0]
        if len(praticas) > 1:
            logger.warning(
                "Fattura %s/%s: %d pratiche diverse in TESFAT (%s), salto il legame.",
                numero_fattura, anno, len(praticas), praticas,
            )
        return None

    def ping(self) -> bool:
        with self._connect() as conn:
            conn.cursor().execute("SELECT COUNT(*) FROM TESFAT").fetchone()
        return True
