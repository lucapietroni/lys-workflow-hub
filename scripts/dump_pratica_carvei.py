"""
Dump di sola lettura di UNA riga della tabella CARVEI (wcArchivi.mdb), per
capire quale campo cambia quando WinCar registra una foto su una pratica.

USO:
1. Modifica ARCHIVIO qui sotto con il percorso reale della cartella Archivi
   di WinCar su questo PC (stesso valore usato da LYS Workflow Hub in .env,
   variabile WINCAR_ARCHIVIO).
2. Scegli una pratica di prova che oggi NON ha la fotocamera nella colonna
   "Foto" dell'elenco pratiche di WinCar.
3. Esegui:  python dump_pratica_carvei.py <numero_pratica>
   Verra' creato "carvei_<numero>_<timestamp>.txt" accanto a questo script.
4. SENZA chiudere WinCar, carica UNA foto su quella pratica nel modo
   normale (dentro WinCar, tasto "Fotografie" o equivalente).
5. Rilancia lo stesso comando: python dump_pratica_carvei.py <numero_pratica>
   Verra' creato un SECONDO file con timestamp diverso.
6. Manda a Claude entrambi i file (o il loro contenuto) per il confronto —
   il campo che cambia tra i due e' quello che WinCar usa per l'icona.

Nessun dato viene modificato: connessione ReadOnly=1, solo SELECT.
"""

import datetime as _dt
import os
import sys

ARCHIVIO = r"C:\Users\lucap\OneDrive\Documenti\Claude\Projects\Lysauto\wincar-sample\Archivi"

DB_FILE = "wcArchivi.mdb"
TABELLA = "CARVEI"
COLONNA_NUMERO = "F_NUMPRA"


def main() -> None:
    if len(sys.argv) != 2:
        print("Uso: python dump_pratica_carvei.py <numero_pratica>")
        sys.exit(1)
    try:
        numero = int(sys.argv[1])
    except ValueError:
        print("Il numero pratica deve essere un intero, es: 836")
        sys.exit(1)

    try:
        import pyodbc
    except ImportError:
        print("ERRORE: la libreria pyodbc non e' installata. Esegui: pip install pyodbc")
        sys.exit(1)

    db_path = os.path.join(ARCHIVIO, DB_FILE)
    if not os.path.isfile(db_path):
        print(f"ERRORE: {db_path} non esiste su questo PC.")
        print("Modifica la variabile ARCHIVIO in cima allo script col percorso reale.")
        sys.exit(1)

    conn_str = (
        r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
        rf"DBQ={db_path};"
        r"ReadOnly=1;"
    )
    conn = pyodbc.connect(conn_str, autocommit=True)
    for enc in ("cp1252", "latin1", "utf-8"):
        try:
            conn.setdecoding(pyodbc.SQL_CHAR, encoding=enc)
            conn.setdecoding(pyodbc.SQL_WCHAR, encoding=enc)
            conn.setencoding(encoding=enc)
            break
        except Exception:
            continue

    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM [{TABELLA}] WHERE [{COLONNA_NUMERO}] = ?", numero)
    row = cursor.fetchone()
    if row is None:
        print(f"Nessuna riga in {TABELLA} con {COLONNA_NUMERO} = {numero}")
        sys.exit(1)

    col_names = [d[0] for d in cursor.description]

    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"carvei_{numero}_{timestamp}.txt"
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"Pratica {numero} — CARVEI — {timestamp}\n")
        out.write("=" * 60 + "\n")
        for name, val in zip(col_names, row):
            try:
                s = repr(val)
            except Exception:
                s = "<non rappresentabile>"
            out.write(f"{name}: {s}\n")

    conn.close()
    print(f"Fatto. Output in {os.path.abspath(out_path)}")


if __name__ == "__main__":
    main()
