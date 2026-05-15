"""Reset dello stato polling M3.

Cancella:
  - tutti i record dalle tabelle `mail_in` e `mail_classificate` del DB SQLite;
  - tutti i file .eml grezzi sotto `APP_ARCHIVIO_MAIL_IN` (default
    `C:\\LYSApp\\Mail_in\\`).

Lascia intatti: pratiche WinCar, compagnie, pec_inviate (cioè le PEC che HAI
inviato), log dell'app.

Usato per "ripartire da zero" col polling, tipicamente DOPO il primo run di
test andato male (es. scaricato tutto lo storico per sbaglio) e PRIMA di
attivare il filtro `MAIL_FETCH_SINCE`.

Uso:
    cd C:\\LYSApp\\lys-workflow-hub
    .venv\\Scripts\\python.exe scripts\\reset_polling.py
    (chiede conferma esplicita prima di procedere)

Per saltare la conferma (es. uso non interattivo):
    .venv\\Scripts\\python.exe scripts\\reset_polling.py --yes
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lys_workflow_hub.config import get_settings  # noqa: E402


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset stato polling M3 (mail_in + .eml)")
    parser.add_argument("--yes", action="store_true",
                        help="Salta la conferma interattiva.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("reset")

    settings = get_settings()
    db_path = Path(settings.app_db_path)
    archivio = Path(settings.app_archivio_mail_in)

    if not db_path.exists():
        log.error("DB non trovato: %s", db_path)
        return 1

    # 1) Conta record attuali
    conn = sqlite3.connect(db_path)
    try:
        n_mail = _count(conn, "mail_in")
        n_class = _count(conn, "mail_classificate")
    finally:
        conn.close()

    # 2) Conta file .eml
    n_eml = 0
    if archivio.exists():
        n_eml = sum(1 for _ in archivio.rglob("*.eml"))

    print()
    print("=== Reset stato polling M3 ===")
    print(f"DB                      : {db_path}")
    print(f"Cartella archivio .eml  : {archivio}")
    print(f"  Record mail_in         : {n_mail}")
    print(f"  Record mail_classificate: {n_class}")
    print(f"  File .eml su disco     : {n_eml}")
    print()
    if n_mail == 0 and n_class == 0 and n_eml == 0:
        print("Nulla da cancellare. Esco.")
        return 0

    if not args.yes:
        risposta = input(
            "Scrivi RESET per confermare la cancellazione (qualsiasi altra cosa annulla): "
        ).strip()
        if risposta != "RESET":
            print("Annullato.")
            return 2

    # 3) Cancella record DB
    log.info("Cancello record mail_classificate e mail_in dal DB...")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM mail_classificate")
        conn.execute("DELETE FROM mail_in")
        # Reset autoincrement per ripartire da id=1 anche su risposte/mail.
        conn.execute("DELETE FROM sqlite_sequence WHERE name='mail_in'")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='mail_classificate'")
        conn.commit()
    except sqlite3.OperationalError as exc:
        log.warning("Impossibile resettare sqlite_sequence: %s", exc)
    finally:
        conn.close()

    # 4) Cancella file .eml + cartella
    if archivio.exists():
        log.info("Cancello cartella %s...", archivio)
        try:
            shutil.rmtree(archivio)
        except OSError as exc:
            log.warning("Errore rimuovendo %s: %s", archivio, exc)

    log.info("Reset completato.")
    print()
    print("Adesso puoi:")
    print(f"  1. Configurare MAIL_FETCH_SINCE in .env (es. 2026-05-15)")
    print(f"  2. Rilanciare il polling: .venv\\Scripts\\python.exe scripts\\run_polling.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
