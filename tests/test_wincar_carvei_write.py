"""Test per `wincar_carvei_write.py` — l'unica scrittura verso il database
di WinCar del progetto. Mai una connessione ODBC reale: `pyodbc.connect` è
sempre mockato, non serve un driver Access installato per eseguire questi
test."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lys_workflow_hub.core.wincar_carvei_write import marca_foto_assente, marca_foto_presente


def _mock_connect(tmp_path: Path):
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


def test_marca_foto_presente_esegue_update_corretto(tmp_path: Path) -> None:
    mock_conn, mock_cursor = _mock_connect(tmp_path)

    with patch("lys_workflow_hub.core.wincar_carvei_write.pyodbc.connect", return_value=mock_conn) as mock_connect:
        marca_foto_presente(
            archivio_root=tmp_path,
            odbc_driver="Microsoft Access Driver (*.mdb, *.accdb)",
            numero_pratica=836,
        )

    # Connessione SENZA ReadOnly=1 — è l'unico punto del progetto che scrive.
    conn_str = mock_connect.call_args.args[0]
    assert "ReadOnly" not in conn_str
    assert str(tmp_path / "wcArchivi.mdb") in conn_str

    # Timeout esplicito sulla query (non solo sul login) — vedi commento nel
    # modulo sotto test sul perché è necessario con WinCar aperto live.
    assert mock_conn.timeout > 0

    sql = mock_cursor.execute.call_args.args[0]
    params = mock_cursor.execute.call_args.args[1:]
    assert "UPDATE CARVEI" in sql
    assert "F_FOTO = ?" in sql
    assert params[0] == -1  # nuovo valore di F_FOTO
    assert params[2] == 836  # F_NUMPRA
    assert params[3] == -1  # guardia WHERE (F_FOTO <> ?)

    mock_conn.close.assert_called_once()


def test_marca_foto_assente_esegue_update_con_valore_zero(tmp_path: Path) -> None:
    mock_conn, mock_cursor = _mock_connect(tmp_path)

    with patch("lys_workflow_hub.core.wincar_carvei_write.pyodbc.connect", return_value=mock_conn):
        marca_foto_assente(
            archivio_root=tmp_path, odbc_driver="driver", numero_pratica=836
        )

    sql = mock_cursor.execute.call_args.args[0]
    params = mock_cursor.execute.call_args.args[1:]
    assert "UPDATE CARVEI" in sql
    assert params[0] == 0
    assert params[2] == 836
    assert params[3] == 0

    mock_conn.close.assert_called_once()


def test_marca_foto_presente_chiude_connessione_anche_se_execute_fallisce(tmp_path: Path) -> None:
    mock_conn, mock_cursor = _mock_connect(tmp_path)
    mock_cursor.execute.side_effect = RuntimeError("tabella bloccata (simulato)")

    with patch("lys_workflow_hub.core.wincar_carvei_write.pyodbc.connect", return_value=mock_conn):
        try:
            marca_foto_presente(
                archivio_root=tmp_path, odbc_driver="driver", numero_pratica=836
            )
        except RuntimeError:
            pass

    mock_conn.close.assert_called_once()
