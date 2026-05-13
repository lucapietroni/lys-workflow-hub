"""Connettore al database di WinCar (Microsoft Access Jet 4).

Apre i file `.mdb` in **sola lettura** via ODBC. Mai una scrittura: il flag
``ReadOnly=1`` nella stringa di connessione, e l'assenza completa di metodi di
INSERT/UPDATE/DELETE in questo modulo, garantiscono l'invariante.

Esempio d'uso:

    repo = WinCarRepository.from_settings()
    pratica = repo.get_pratica(766)
    print(pratica.cliente.nome, pratica.sinistro.data)

Nota: questo modulo *deve* girare su Windows con il driver Microsoft Access
Database Engine installato. Su Linux/macOS solleva un'eccezione esplicativa al
primo tentativo di connessione.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

try:
    import pyodbc
except ImportError:  # pragma: no cover
    pyodbc = None  # type: ignore[assignment]

from lys_workflow_hub.config import Settings, get_settings


# -----------------------------------------------------------------------------
# Modelli di dominio
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Cliente:
    nominativo: str
    codice_fiscale: str
    partita_iva: str
    via: str
    citta: str
    cap: str
    provincia: str
    telefono: str
    cellulare: str
    email: str


@dataclass(frozen=True)
class Veicolo:
    targa: str
    marca: str
    modello: str
    telaio: str


@dataclass(frozen=True)
class Sinistro:
    data: date | None
    ora: str
    comune: str
    via: str
    dinamica: str
    numero: str
    tipo: str


@dataclass(frozen=True)
class Controparte:
    proprietario: str
    conducente: str
    veicolo_descrizione: str
    targa: str
    indirizzo: str
    citta: str
    compagnia: str
    numero_polizza: str


@dataclass(frozen=True)
class CompagniaCliente:
    """Compagnia assicurativa del cliente stesso (utile per il workflow Vandalismo)."""
    nome: str
    indirizzo: str
    citta: str
    cap: str
    provincia: str
    numero_polizza: str
    agenzia: str


@dataclass(frozen=True)
class Pratica:
    """Vista pulita di una riga della tabella CARVEI di wcArchivi.mdb."""
    numero: int
    data_creazione: datetime | None
    cliente: Cliente
    veicolo: Veicolo
    sinistro: Sinistro
    controparte: Controparte
    assicurazione_cliente: CompagniaCliente

    def cartella_pratica(self, archivio_root: Path) -> Path:
        """Restituisce la cartella su filesystem corrispondente a questa pratica."""
        return archivio_root / "Pratiche" / str(self.numero)


# -----------------------------------------------------------------------------
# Repository
# -----------------------------------------------------------------------------


class WinCarRepository:
    """Lettore read-only del database di WinCar.

    Non condivide connessioni tra thread: usa `with repo.connect() as conn` per ogni
    operazione di lettura. Il driver Access tollera bene la concorrenza in lettura.
    """

    # Mappa dei file MDB principali, rispetto alla cartella Archivio.
    _DB_FILE_ARCHIVI = "wcArchivi.mdb"

    def __init__(self, archivio_root: Path, odbc_driver: str) -> None:
        self.archivio_root = Path(archivio_root)
        self.odbc_driver = odbc_driver

    # -- factory ----------------------------------------------------------

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "WinCarRepository":
        s = settings or get_settings()
        return cls(archivio_root=s.wincar_archivio, odbc_driver=s.wincar_odbc_driver)

    # -- connessione ------------------------------------------------------

    def _connection_string(self, db_file: str) -> str:
        db_path = self.archivio_root / db_file
        return (
            f"DRIVER={{{self.odbc_driver}}};"
            f"DBQ={db_path};"
            "ReadOnly=1;"
        )

    @contextmanager
    def connect(self, db_file: str = _DB_FILE_ARCHIVI) -> Iterator["pyodbc.Connection"]:
        """Apre una connessione ODBC in sola lettura sul .mdb indicato."""
        if pyodbc is None:
            raise RuntimeError(
                "pyodbc non installato. Su Windows: pip install pyodbc"
            )
        conn = pyodbc.connect(self._connection_string(db_file), autocommit=True)
        # Forzo la codifica cp1252 sul driver Access italiano (vedi analisi v2 §4.1).
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

    # -- query principali (TODO M1) ---------------------------------------

    def get_pratica(self, numero: int) -> Pratica | None:  # pragma: no cover - TODO
        """Restituisce la Pratica con il numero indicato, o None se non esiste.

        Implementazione prevista per M1. Lo scheletro è qui per fissare il
        contratto verso i workflow.
        """
        raise NotImplementedError("Implementazione prevista in M1 (Fondazione + Workflow A).")

    def search_pratiche(
        self,
        *,
        cognome: str | None = None,
        targa: str | None = None,
        numero: int | None = None,
        limit: int = 20,
    ) -> list[Pratica]:  # pragma: no cover - TODO
        """Ricerca pratiche per cognome, targa o numero (anche parziale)."""
        raise NotImplementedError("Implementazione prevista in M1 (Fondazione + Workflow A).")
