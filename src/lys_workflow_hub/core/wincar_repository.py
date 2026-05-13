"""Connettore al database di WinCar (Microsoft Access Jet 4).

Apre i file `.mdb` in **sola lettura** via ODBC. Mai una scrittura: il flag
``ReadOnly=1`` nella stringa di connessione, e l'assenza completa di metodi di
INSERT/UPDATE/DELETE in questo modulo, garantiscono l'invariante.

Esempio d'uso:

    repo = WinCarRepository.from_settings()
    risultati = repo.search_pratiche(cognome="rossi")
    if risultati:
        pratica = repo.get_pratica(risultati[0].numero)
        print(pratica.cliente.nominativo, pratica.sinistro.data)

Questo modulo deve girare su Windows con il driver Microsoft Access Database
Engine installato. Su Linux/macOS solleva un'eccezione esplicativa quando si
tenta di connettersi.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

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
    codice_fiscale: str | None
    partita_iva: str | None
    via: str | None
    citta: str | None
    cap: str | None
    provincia: str | None
    telefono: str | None
    cellulare: str | None
    email: str | None


@dataclass(frozen=True)
class Veicolo:
    targa: str
    marca: str | None
    modello: str | None
    telaio: str | None


@dataclass(frozen=True)
class Sinistro:
    data: date | None
    ora: str | None
    comune: str | None
    via: str | None
    dinamica: str | None
    numero: str | None
    tipo: str | None


@dataclass(frozen=True)
class Controparte:
    proprietario: str | None
    conducente: str | None
    veicolo_descrizione: str | None
    targa: str | None
    indirizzo: str | None
    citta: str | None
    compagnia: str | None
    numero_polizza: str | None


@dataclass(frozen=True)
class CompagniaCliente:
    """Compagnia assicurativa del cliente stesso (utile per il workflow Vandalismo)."""

    nome: str | None
    indirizzo: str | None
    citta: str | None
    cap: str | None
    provincia: str | None
    numero_polizza: str | None
    agenzia: str | None


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


@dataclass(frozen=True)
class PraticaSummary:
    """Riassunto compatto di una pratica, usato nelle liste di ricerca."""

    numero: int
    cliente_nominativo: str
    targa: str
    marca: str | None
    modello: str | None
    data_sinistro: date | None
    codice_fiscale: str | None


# Colonne del SELECT compatto per la ricerca (in ordine).
_SUMMARY_COLUMNS = (
    "F_NUMPRA",
    "F_RAGSOC",
    "F_TARGAV",
    "F_DESMAR",
    "F_DESMOD",
    "F_DATASI",
    "F_CODFIS",
)


# -----------------------------------------------------------------------------
# Helper
# -----------------------------------------------------------------------------


def _none_if_empty(value: Any) -> Any:
    """Converte stringhe vuote in None; lascia tutto il resto invariato."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _as_date(value: Any) -> date | None:
    """Estrae la data (senza ora) da un valore datetime di Access."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


# -----------------------------------------------------------------------------
# Repository
# -----------------------------------------------------------------------------


class WinCarRepository:
    """Lettore read-only del database di WinCar."""

    DB_FILE_ARCHIVI = "wcArchivi.mdb"

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
    def connect(self, db_file: str = DB_FILE_ARCHIVI) -> Iterator["pyodbc.Connection"]:
        """Apre una connessione ODBC in sola lettura sul `.mdb` indicato."""
        if pyodbc is None:
            raise RuntimeError(
                "pyodbc non installato. Su Windows esegui: pip install pyodbc"
            )
        conn = pyodbc.connect(self._connection_string(db_file), autocommit=True)
        # Forza la codifica cp1252 sul driver Access italiano (vedi analisi v2 §4.1).
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

    # -- ricerca pratiche -------------------------------------------------

    def search_pratiche(
        self,
        *,
        cognome: str | None = None,
        targa: str | None = None,
        numero: int | None = None,
        limit: int = 20,
    ) -> list[PraticaSummary]:
        """Cerca pratiche per cognome/ragione sociale, targa o numero.

        - I filtri si combinano in AND.
        - Le stringhe accettano match parziale (LIKE %valore%) e sono case-insensitive.
        - Senza filtri restituisce le `limit` pratiche più recenti (numero più alto).
        """
        where_clauses: list[str] = []
        params: list[Any] = []

        if numero is not None:
            where_clauses.append("F_NUMPRA = ?")
            params.append(int(numero))

        if targa:
            where_clauses.append("UCASE(F_TARGAV) LIKE ?")
            params.append(f"%{targa.upper().strip()}%")

        if cognome:
            where_clauses.append("LCASE(F_RAGSOC) LIKE ?")
            params.append(f"%{cognome.lower().strip()}%")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        columns_sql = ", ".join(f"[{c}]" for c in _SUMMARY_COLUMNS)
        sql = (
            f"SELECT TOP {int(limit)} {columns_sql} "
            f"FROM CARVEI WHERE {where_sql} "
            "ORDER BY F_NUMPRA DESC"
        )

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        return [self._row_to_summary(dict(zip(_SUMMARY_COLUMNS, row))) for row in rows]

    # -- dettaglio pratica -----------------------------------------------

    def get_pratica(self, numero: int) -> Pratica | None:
        """Restituisce la pratica con il numero indicato, o None se non esiste."""
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM CARVEI WHERE F_NUMPRA = ?", (int(numero),))
            row = cursor.fetchone()
            if row is None:
                return None
            colnames = [d[0] for d in cursor.description]
            data = dict(zip(colnames, row))
        return self._row_to_pratica(data)

    # -- ping / verifica connettività ------------------------------------

    def ping(self) -> bool:
        """Verifica che il DB sia accessibile aprendo e chiudendo una connessione."""
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM CARVEI")
            cursor.fetchone()
        return True

    # -- mapping interno -------------------------------------------------

    @staticmethod
    def _row_to_summary(data: dict[str, Any]) -> PraticaSummary:
        return PraticaSummary(
            numero=int(data["F_NUMPRA"]),
            cliente_nominativo=(_none_if_empty(data["F_RAGSOC"]) or ""),
            targa=(_none_if_empty(data["F_TARGAV"]) or ""),
            marca=_none_if_empty(data["F_DESMAR"]),
            modello=_none_if_empty(data["F_DESMOD"]),
            data_sinistro=_as_date(data["F_DATASI"]),
            codice_fiscale=_none_if_empty(data["F_CODFIS"]),
        )

    @staticmethod
    def _row_to_pratica(data: dict[str, Any]) -> Pratica:
        cliente = Cliente(
            nominativo=(_none_if_empty(data.get("F_RAGSOC")) or ""),
            codice_fiscale=_none_if_empty(data.get("F_CODFIS")),
            partita_iva=_none_if_empty(data.get("F_PARIVA")),
            via=_none_if_empty(data.get("F_VIACLI")),
            citta=_none_if_empty(data.get("F_CITTAC")),
            cap=_none_if_empty(data.get("F_CAPCLI")),
            provincia=_none_if_empty(data.get("F_PROCLI")),
            telefono=_none_if_empty(data.get("F_TELEFO")),
            cellulare=_none_if_empty(data.get("F_CELLUL")),
            email=_none_if_empty(data.get("F__EMAIL")),
        )
        veicolo = Veicolo(
            targa=(_none_if_empty(data.get("F_TARGAV")) or ""),
            marca=_none_if_empty(data.get("F_DESMAR")),
            modello=_none_if_empty(data.get("F_DESMOD")),
            telaio=_none_if_empty(data.get("F_TELAIO")),
        )
        sinistro = Sinistro(
            data=_as_date(data.get("F_DATASI")),
            ora=_none_if_empty(data.get("F_ORASIN")),
            comune=_none_if_empty(data.get("F_LOCSIN")),
            via=_none_if_empty(data.get("F_VIASIN")),
            dinamica=_none_if_empty(data.get("F_MODSIN")),
            numero=_none_if_empty(data.get("F_NUMSIN")),
            tipo=_none_if_empty(data.get("F_TIPSIN")),
        )
        controparte = Controparte(
            proprietario=_none_if_empty(data.get("F_NOMECO")),
            conducente=_none_if_empty(data.get("F_CONDUC")),
            veicolo_descrizione=_none_if_empty(data.get("F_MACCON")),
            targa=_none_if_empty(data.get("F_TARCON")),
            indirizzo=_none_if_empty(data.get("F_INDCON")),
            citta=_none_if_empty(data.get("F_CITCON")),
            compagnia=_none_if_empty(data.get("F_DEASCO")),
            numero_polizza=_none_if_empty(data.get("F_NUMPO2")),
        )
        ass_cliente = CompagniaCliente(
            nome=_none_if_empty(data.get("F_DEASCL")),
            indirizzo=_none_if_empty(data.get("F_INDASS")),
            citta=_none_if_empty(data.get("F_CITASS")),
            cap=_none_if_empty(data.get("F_CAPASS")),
            provincia=_none_if_empty(data.get("F_PROASS")),
            numero_polizza=_none_if_empty(data.get("F_NUMPOL")),
            agenzia=_none_if_empty(data.get("F_AGECLI")),
        )

        data_creazione_raw = data.get("F_DATACA")
        if isinstance(data_creazione_raw, datetime):
            data_creazione: datetime | None = data_creazione_raw
        elif isinstance(data_creazione_raw, date):
            data_creazione = datetime.combine(data_creazione_raw, datetime.min.time())
        else:
            data_creazione = None

        return Pratica(
            numero=int(data["F_NUMPRA"]),
            data_creazione=data_creazione,
            cliente=cliente,
            veicolo=veicolo,
            sinistro=sinistro,
            controparte=controparte,
            assicurazione_cliente=ass_cliente,
        )
