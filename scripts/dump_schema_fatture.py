"""Ricognizione schema di wcFatture.mdb (modulo Fatturazione elettronica WinCar).

Serve a capire in quale tabella/colonna WinCar tiene il legame fattura →
numero pratica, così da poterlo leggere via ODBC in sola lettura (come già si
fa per le pratiche in `wincar_repository.py`).

USO (sul PC carrozzeria, dove gira WinCar):

    cd C:\\LYSApp\\lys-workflow-hub
    .venv\\Scripts\\python.exe scripts\\dump_schema_fatture.py

Oppure con un percorso diverso:

    python scripts\\dump_schema_fatture.py "C:\\WinCar\\Archivi\\wcFatture.mdb"

Crea un file `wcFatture_schema_<timestamp>.txt` accanto allo script: aprilo con
Blocco note e incollane il contenuto qui in chat.

Nessun dato viene modificato: il DB è aperto in sola lettura.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import traceback

DEFAULT_PATH = r"C:\WinCar\Archivi\wcFatture.mdb"
# Quante righe di esempio dumpare per tabella (per riconoscere le colonne).
SAMPLE_ROWS = 3
MAX_VALUE_LEN = 160


def _connect(path: str):
    import pyodbc

    conn = pyodbc.connect(
        r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
        rf"DBQ={path};ReadOnly=1;",
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
    return conn


def dump(path: str, out) -> None:
    out.write("=" * 80 + "\n")
    out.write(f"DATABASE: {path}\n")
    out.write("=" * 80 + "\n\n")

    try:
        import pyodbc  # noqa: F401
    except ImportError:
        out.write("ERRORE: pyodbc non installato. Esegui: pip install pyodbc\n")
        return

    try:
        conn = _connect(path)
    except Exception as exc:
        out.write(f"Impossibile aprire il DB: {exc}\n")
        out.write("Cause probabili: driver Access mancante o bitness errata.\n")
        return

    cursor = conn.cursor()
    try:
        tables = sorted(
            row.table_name
            for row in cursor.tables(tableType="TABLE")
            if not row.table_name.startswith("MSys")
        )
    except Exception as exc:
        out.write(f"Elenco tabelle fallito: {exc}\n")
        conn.close()
        return

    out.write(f"Tabelle: {len(tables)}\n  " + "\n  ".join(tables) + "\n\n")

    for t in tables:
        print(f"  - {t}", flush=True)
        out.write("-" * 60 + "\n")
        out.write(f"TABELLA: {t}\n")
        out.write("-" * 60 + "\n")
        col_names: list[str] = []
        try:
            cur2 = conn.cursor()
            cur2.execute(f"SELECT TOP 1 * FROM [{t}]")
            desc = cur2.description
            col_names = [d[0] for d in desc]
            out.write("Colonne:\n")
            for d in desc:
                tname = d[1].__name__ if hasattr(d[1], "__name__") else str(d[1])
                out.write(f"  - {d[0]}  ({tname}, size={d[3]})\n")
            cur2.close()
        except Exception as exc:
            out.write(f"Lettura colonne fallita: {exc}\n")

        try:
            n = cursor.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
            out.write(f"Righe: {n}\n")
        except Exception as exc:
            out.write(f"Conteggio righe fallito: {exc}\n")
            n = 0

        if n and col_names:
            try:
                rows = cursor.execute(
                    f"SELECT TOP {SAMPLE_ROWS} * FROM [{t}]"
                ).fetchall()
                out.write(f"Esempi (max {SAMPLE_ROWS}):\n")
                for r in rows:
                    out.write("  {\n")
                    for name, val in zip(col_names, r):
                        try:
                            s = repr(val)
                        except Exception:
                            s = "<non rappresentabile>"
                        if len(s) > MAX_VALUE_LEN:
                            s = s[:MAX_VALUE_LEN] + "...<troncato>"
                        try:
                            out.write(f"    {name}: {s}\n")
                        except UnicodeEncodeError:
                            out.write(f"    {name}: <unicode error>\n")
                    out.write("  }\n")
            except Exception as exc:
                out.write(f"Lettura esempi fallita: {exc}\n")
        out.write("\n")
        out.flush()

    conn.close()


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    out_name = f"wcFatture_schema_{_dt.datetime.now():%Y%m%d_%H%M%S}.txt"
    with open(out_name, "w", encoding="utf-8") as out:
        if not os.path.isfile(path):
            out.write(f"ERRORE: file non trovato: {path}\n")
            out.write("Passa il percorso corretto come argomento.\n")
        else:
            try:
                dump(path, out)
            except Exception:
                out.write("Errore inatteso:\n" + traceback.format_exc())
    print(f"Fatto. Output in {os.path.abspath(out_name)}")


if __name__ == "__main__":
    main()
