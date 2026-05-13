"""
Script di ricognizione schema WinCar.

USO:
1. Assicurati di avere Python installato (https://www.python.org/downloads/  -> spunta "Add to PATH").
2. Apri il Prompt dei comandi e installa la libreria:
       pip install pyodbc
3. Se NON hai Microsoft Access installato sul PC, scarica e installa il driver gratuito:
       "Microsoft Access Database Engine 2016 Redistributable"  (versione a 64 bit, che corrisponda al tuo Python).
4. Esegui:
       python dump_schema_wincar.py
5. Verra' creato accanto a questo script il file "wincar_schema.txt".
   Apri quel file con Blocco note e incolla il contenuto a Claude.

Nessun dato viene modificato: lo script apre i DB in sola lettura.
"""

import os
import sys
import traceback

ARCHIVIO = r"C:\Users\lucap\OneDrive\Documenti\Claude\Projects\Lysauto\wincar-sample\Archivi"

# Database piu' interessanti per il modulo Cessione del Credito.
DB_FILES = [
    "wcArchivi.mdb",       # pratiche, sinistri, anagrafiche denormalizzate
    "wcAttivita.mdb",      # log attivita' per pratica
    "wcAllegati.mdb",      # registro allegati
    "wcTarghe.mdb",        # storico targhe/veicoli
    "wcGenerici.mdb",      # listino voci generiche
    "wcTabelle.mdb",       # tabelle di lookup (comuni, marche, modelli, ecc.) - 34 MB
]

import datetime as _dt
OUTPUT = f"wincar_schema_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"


def log(msg):
    print(msg, flush=True)


def dump_db(path, out):
    try:
        import pyodbc
    except ImportError:
        out.write("ERRORE: la libreria pyodbc non e' installata.\n")
        out.write("Esegui:  pip install pyodbc\n")
        return

    conn_str = (
        r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
        rf"DBQ={path};"
        r"ReadOnly=1;"
    )
    out.write("\n" + "=" * 80 + "\n")
    out.write(f"DATABASE: {path}\n")
    out.write("=" * 80 + "\n")
    out.flush()
    log(f"\n>>> Apertura {os.path.basename(path)}")

    try:
        conn = pyodbc.connect(conn_str, autocommit=True)
    except Exception as e:
        out.write(f"Impossibile aprire il DB: {e}\n")
        out.write("Probabili cause: driver Access mancante o bitness errata (Python 64-bit vs driver 32-bit).\n")
        return

    # Workaround codifica: il driver Access su Windows italiano restituisce
    # spesso testo in cp1252, non in utf-16. Forziamo entrambe le direzioni.
    for enc in ("cp1252", "latin1", "utf-8"):
        try:
            conn.setdecoding(pyodbc.SQL_CHAR, encoding=enc)
            conn.setdecoding(pyodbc.SQL_WCHAR, encoding=enc)
            conn.setencoding(encoding=enc)
            break
        except Exception:
            continue

    cursor = conn.cursor()
    # Elenca tabelle utente (esclude le tabelle di sistema MSys*).
    try:
        tables = [
            row.table_name
            for row in cursor.tables(tableType="TABLE")
            if not row.table_name.startswith("MSys")
        ]
    except Exception as e:
        out.write(f"Elenco tabelle fallito: {e}\n")
        conn.close()
        return
    out.write(f"Tabelle trovate: {len(tables)}\n\n")

    for t in tables:
        log(f"  - tabella: {t}")
        try:
            out.write("-" * 60 + "\n")
            out.write(f"TABELLA: {t}\n")
            out.write("-" * 60 + "\n")

            # Per evitare l'errore di codifica su cursor.columns(), prendiamo
            # i nomi delle colonne da cursor.description dopo un SELECT TOP 1.
            col_names = []
            try:
                cur2 = conn.cursor()
                cur2.execute(f"SELECT TOP 1 * FROM [{t}]")
                cols_desc = cur2.description
                col_names = [d[0] for d in cols_desc]
                out.write("Colonne:\n")
                for d in cols_desc:
                    tname = d[1].__name__ if hasattr(d[1], "__name__") else str(d[1])
                    out.write(f"  - {d[0]}  ({tname}, size={d[3]})\n")
                cur2.close()
            except Exception as e:
                out.write(f"Lettura colonne fallita: {e}\n")

            # Conteggio righe
            try:
                n = cursor.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                out.write(f"Numero righe: {n}\n")
            except Exception as e:
                out.write(f"Conteggio righe fallito: {e}\n")
                n = 0

            # Due righe di esempio (oscurando eventuali valori molto lunghi)
            if n > 0 and col_names:
                try:
                    rows = cursor.execute(f"SELECT TOP 2 * FROM [{t}]").fetchall()
                    out.write("Esempi (max 2 righe):\n")
                    for r in rows:
                        out.write("  {\n")
                        for name, val in zip(col_names, r):
                            try:
                                s = repr(val)
                            except Exception:
                                s = "<non rappresentabile>"
                            if len(s) > 120:
                                s = s[:120] + "...<troncato>"
                            try:
                                out.write(f"    {name}: {s}\n")
                            except UnicodeEncodeError:
                                # Fallback se il repr contiene caratteri non codificabili
                                out.write(f"    {name}: <unicode error>\n")
                        out.write("  }\n")
                except Exception as e:
                    out.write(f"Lettura esempi fallita: {e}\n")
            out.write("\n")
        except Exception:
            out.write(f"ERRORE su tabella {t}:\n")
            out.write(traceback.format_exc() + "\n")
        finally:
            # Scarico su disco dopo ogni tabella: se crasha non perdiamo nulla
            out.flush()
            try:
                os.fsync(out.fileno())
            except Exception:
                pass

    conn.close()
    log(f"<<< Chiuso {os.path.basename(path)}")


def main():
    seen = set()
    with open(OUTPUT, "w", encoding="utf-8") as out:
        out.write(f"Ricognizione cartella: {ARCHIVIO}\n")
        if not os.path.isdir(ARCHIVIO):
            out.write("ERRORE: la cartella non esiste su questo PC.\n")
            out.write("Modifica la variabile ARCHIVIO in cima allo script.\n")
            return
        for name in DB_FILES:
            if name in seen:
                continue
            seen.add(name)
            path = os.path.join(ARCHIVIO, name)
            if not os.path.isfile(path):
                out.write(f"\n[saltato] {path} non esiste\n")
                continue
            try:
                dump_db(path, out)
            except Exception:
                out.write(f"Errore inatteso su {path}:\n")
                out.write(traceback.format_exc())
    print(f"Fatto. Output in {os.path.abspath(OUTPUT)}")


if __name__ == "__main__":
    main()
