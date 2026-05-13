"""Test unitari del connettore WinCar usando pyodbc mockato.

Questi test girano ovunque (Linux, macOS, CI) senza bisogno del vero driver Access:
sostituiamo `pyodbc.connect` con un mock che restituisce dati di esempio.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lys_workflow_hub.core.wincar_repository import (
    Pratica,
    PraticaSummary,
    WinCarRepository,
    _none_if_empty,
    _as_date,
)


# Riga di esempio basata sulla struttura reale della tabella CARVEI.
SAMPLE_ROW_DICT = {
    "F_NUMPRA": 766,
    "F_DATACA": datetime(2026, 5, 10, 14, 30),
    "F_TARGAV": "AB123CD",
    "F_DESMAR": "FIAT",
    "F_DESMOD": "Punto 1.2 dal 2016",
    "F_TELAIO": "ZFA199000000001",
    "F_CODCLI": 0,
    "F_RAGSOC": "rossi mario",
    "F_VIACLI": "Via Roma 12",
    "F_CITTAC": "Roma",
    "F_CAPCLI": "00100",
    "F_PROCLI": "RM",
    "F_PARIVA": "",
    "F_CODFIS": "RSSMRA80A01H501U",
    "F_TELEFO": "0612345678",
    "F_CELLUL": "3331234567",
    "F__EMAIL": "mario.rossi@example.com",
    "F_DATASI": datetime(2026, 5, 8, 10, 15),
    "F_ORASIN": "10:15",
    "F_LOCSIN": "Roma",
    "F_VIASIN": "Via Nazionale",
    "F_MODSIN": "Tamponamento posteriore al semaforo.",
    "F_TIPSIN": "C",
    "F_NUMSIN": "2026-AB-001",
    "F_NOMECO": "BIANCHI LUCA",
    "F_INDCON": "Via Veneto 5",
    "F_CITCON": "Roma",
    "F_MACCON": "BMW Serie 1",
    "F_TARCON": "XY987ZW",
    "F_CONDUC": "BIANCHI LUCA",
    "F_DEASCO": "Generali Italia SpA",
    "F_NUMPO2": "POL-99887766",
    "F_DEASCL": "Allianz",
    "F_INDASS": "Via Larga 3",
    "F_CITASS": "Milano",
    "F_CAPASS": "20100",
    "F_PROASS": "MI",
    "F_NUMPOL": "POL-11223344",
    "F_AGECLI": "AG-ROMA-01",
}


def _row_for_columns(columns: tuple[str, ...]) -> tuple:
    """Costruisce una tupla nell'ordine delle colonne richieste."""
    return tuple(SAMPLE_ROW_DICT[c] for c in columns)


def _description_for(columns: tuple[str, ...]) -> list[tuple]:
    """Simula cursor.description nello stesso formato pyodbc."""
    return [(c, str, None, 50, 0, 0, True) for c in columns]


@pytest.fixture
def mock_pyodbc():
    """Mock di pyodbc.connect che restituisce dati controllati."""
    with patch("lys_workflow_hub.core.wincar_repository.pyodbc") as mocked:
        # `connect()` -> connection con cursor() -> cursor che usiamo per execute/fetch
        conn = MagicMock(name="connection")
        cursor = MagicMock(name="cursor")
        conn.cursor.return_value = cursor
        mocked.connect.return_value = conn
        # SQL_CHAR / SQL_WCHAR non li usiamo davvero, basta che esistano sull'oggetto
        mocked.SQL_CHAR = 0
        mocked.SQL_WCHAR = 0
        yield mocked, conn, cursor


@pytest.fixture
def repo(tmp_path: Path) -> WinCarRepository:
    return WinCarRepository(archivio_root=tmp_path, odbc_driver="Test Driver")


# ---------------------------------------------------------------------------
# helper puri
# ---------------------------------------------------------------------------


def test_none_if_empty_handles_strings():
    assert _none_if_empty("") is None
    assert _none_if_empty("   ") is None
    assert _none_if_empty("ciao") == "ciao"
    assert _none_if_empty("  ciao  ") == "ciao"


def test_none_if_empty_passes_through_non_strings():
    assert _none_if_empty(None) is None
    assert _none_if_empty(0) == 0
    assert _none_if_empty(123) == 123


def test_as_date_extracts_date_from_datetime():
    d = _as_date(datetime(2026, 5, 8, 10, 15))
    assert d is not None
    assert d.year == 2026 and d.month == 5 and d.day == 8


def test_as_date_returns_none_for_none():
    assert _as_date(None) is None


# ---------------------------------------------------------------------------
# WinCarRepository.search_pratiche
# ---------------------------------------------------------------------------


def test_search_returns_pratica_summary(repo, mock_pyodbc):
    _, _, cursor = mock_pyodbc
    summary_cols = ("F_NUMPRA", "F_RAGSOC", "F_TARGAV", "F_DESMAR", "F_DESMOD", "F_DATASI", "F_CODFIS")
    cursor.fetchall.return_value = [_row_for_columns(summary_cols)]

    results = repo.search_pratiche(limit=5)

    assert len(results) == 1
    s = results[0]
    assert isinstance(s, PraticaSummary)
    assert s.numero == 766
    assert s.cliente_nominativo == "rossi mario"
    assert s.targa == "AB123CD"
    assert s.marca == "FIAT"
    assert s.codice_fiscale == "RSSMRA80A01H501U"
    # data_sinistro deve essere una date pulita, senza ora
    assert s.data_sinistro is not None and s.data_sinistro.year == 2026


def test_search_passes_correct_sql_and_params(repo, mock_pyodbc):
    _, _, cursor = mock_pyodbc
    cursor.fetchall.return_value = []

    repo.search_pratiche(cognome="ROSSI", targa="ab123", numero=10, limit=7)

    args, _kwargs = cursor.execute.call_args
    sql, params = args
    assert "TOP 7" in sql
    assert "ORDER BY F_NUMPRA DESC" in sql
    # I tre filtri sono in AND e arrivano come parametri ?
    assert "F_NUMPRA = ?" in sql
    assert "UCASE(F_TARGAV) LIKE ?" in sql
    assert "LCASE(F_RAGSOC) LIKE ?" in sql
    assert params == [10, "%AB123%", "%rossi%"]


def test_search_empty_filters_returns_last_n(repo, mock_pyodbc):
    _, _, cursor = mock_pyodbc
    cursor.fetchall.return_value = []

    repo.search_pratiche(limit=3)

    args, _kwargs = cursor.execute.call_args
    sql, params = args
    assert "WHERE 1=1" in sql
    assert params == []


# ---------------------------------------------------------------------------
# WinCarRepository.get_pratica
# ---------------------------------------------------------------------------


def test_get_pratica_returns_full_object(repo, mock_pyodbc):
    _, _, cursor = mock_pyodbc
    cols = tuple(SAMPLE_ROW_DICT.keys())
    cursor.fetchone.return_value = _row_for_columns(cols)
    cursor.description = _description_for(cols)

    pratica = repo.get_pratica(766)

    assert pratica is not None
    assert isinstance(pratica, Pratica)
    assert pratica.numero == 766
    assert pratica.cliente.nominativo == "rossi mario"
    assert pratica.cliente.codice_fiscale == "RSSMRA80A01H501U"
    assert pratica.cliente.partita_iva is None  # stringa vuota normalizzata a None
    assert pratica.veicolo.targa == "AB123CD"
    assert pratica.sinistro.dinamica == "Tamponamento posteriore al semaforo."
    assert pratica.controparte.compagnia == "Generali Italia SpA"
    assert pratica.controparte.numero_polizza == "POL-99887766"
    assert pratica.assicurazione_cliente.nome == "Allianz"
    assert pratica.assicurazione_cliente.numero_polizza == "POL-11223344"


def test_get_pratica_returns_none_if_not_found(repo, mock_pyodbc):
    _, _, cursor = mock_pyodbc
    cursor.fetchone.return_value = None

    assert repo.get_pratica(99999) is None


def test_get_pratica_cartella_path(repo, mock_pyodbc, tmp_path):
    _, _, cursor = mock_pyodbc
    cols = tuple(SAMPLE_ROW_DICT.keys())
    cursor.fetchone.return_value = _row_for_columns(cols)
    cursor.description = _description_for(cols)

    pratica = repo.get_pratica(766)
    assert pratica is not None
    assert pratica.cartella_pratica(tmp_path) == tmp_path / "Pratiche" / "766"
