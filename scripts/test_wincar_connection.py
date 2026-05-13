"""Script di verifica del connettore WinCar (M1.1).

Da eseguire dal Prompt comandi dopo aver installato i requirements e configurato
il file .env. Stampa a video tre cose, in sequenza:

  1) Schema check (sono presenti tutte le colonne richieste?)
  2) Ricerca: ultime 5 pratiche per numero decrescente.
  3) Dettaglio della pratica piu' recente.

Nessun dato viene mai modificato. Tutte le query sono read-only.

Esecuzione:
    cd lys-workflow-hub
    python scripts/test_wincar_connection.py
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

# Permettere l'esecuzione senza installare il pacchetto, aggiungendo src/ al path.
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lys_workflow_hub.config import get_settings  # noqa: E402
from lys_workflow_hub.core.schema_check import run_schema_check  # noqa: E402
from lys_workflow_hub.core.wincar_repository import WinCarRepository  # noqa: E402


SEP = "=" * 72


def banner(text: str) -> None:
    print(f"\n{SEP}\n{text}\n{SEP}")


def main() -> int:
    settings = get_settings()
    banner(f"Configurazione attiva  (env={settings.app_env})")
    print(f"  WinCar archivio: {settings.wincar_archivio}")
    print(f"  ODBC driver:     {settings.wincar_odbc_driver}")
    if not settings.wincar_archivio.exists():
        print(f"\nERRORE: la cartella '{settings.wincar_archivio}' non esiste.")
        print("Modifica WINCAR_ARCHIVIO nel file .env e riprova.")
        return 2

    repo = WinCarRepository.from_settings(settings)

    # 1) Schema check ------------------------------------------------------
    banner("1) Schema check")
    try:
        result = run_schema_check(repo)
    except Exception as exc:  # noqa: BLE001
        print(f"Schema check NON eseguito: {exc}")
        return 3
    print(result.explain())
    if result.extra:
        print("\nNota: colonne extra rispetto all'atteso (informativo, non bloccante):")
        for table, cols in result.extra.items():
            print(f"  - {table}: {len(cols)} colonne extra")
    if not result.ok:
        print("\nProsecuzione comunque, per consentire la diagnosi.")

    # 2) Ricerca ----------------------------------------------------------
    banner("2) Ultime 5 pratiche")
    try:
        ultime = repo.search_pratiche(limit=5)
    except Exception as exc:  # noqa: BLE001
        print(f"Errore durante la ricerca: {exc}")
        return 4
    if not ultime:
        print("Nessuna pratica trovata. (Il database e' vuoto o filtrato?)")
        return 0

    print(f"{'NUMPRA':>8} | {'TARGA':<10} | {'NOMINATIVO':<30} | {'DATA SIN':<10} | MARCA/MODELLO")
    print("-" * 95)
    for s in ultime:
        data = s.data_sinistro.isoformat() if s.data_sinistro else "—"
        marca_modello = " ".join(filter(None, [s.marca, s.modello]))[:40]
        print(
            f"{s.numero:>8} | {s.targa:<10} | "
            f"{s.cliente_nominativo[:30]:<30} | {data:<10} | {marca_modello}"
        )

    # 3) Dettaglio prima pratica ------------------------------------------
    target_numero = ultime[0].numero
    banner(f"3) Dettaglio pratica {target_numero}")
    pratica = repo.get_pratica(target_numero)
    if pratica is None:
        print(f"Pratica {target_numero} non trovata.")
        return 5

    def show_section(title: str, obj: object) -> None:
        print(f"\n[{title}]")
        for key, value in asdict(obj).items():
            print(f"  {key:<22}: {value}")

    print(f"Numero pratica: {pratica.numero}")
    print(f"Data creazione: {pratica.data_creazione}")
    print(f"Cartella su disco: {pratica.cartella_pratica(settings.wincar_archivio)}")
    show_section("Cliente", pratica.cliente)
    show_section("Veicolo", pratica.veicolo)
    show_section("Sinistro", pratica.sinistro)
    show_section("Controparte", pratica.controparte)
    show_section("Assicurazione cliente", pratica.assicurazione_cliente)

    banner("Tutto ok. Connettore WinCar funzionante.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
