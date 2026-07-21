"""Crea (o promuove) un utente amministratore — bootstrap del sistema di login.

Necessario dopo il primo deploy della v3.0 (autenticazione): senza almeno un
admin nessuno può entrare nell'app, dato che non esiste self-registration.

Uso:
    cd C:\\LYSApp\\lys-workflow-hub
    .venv\\Scripts\\python.exe scripts\\create_admin.py

Chiede email, nome e password interattivamente (password non echeggiata,
tramite `getpass`). Se l'email esiste già, chiede conferma e resetta la
password + promuove a ruolo admin.

Per uso non interattivo (es. script di provisioning):
    .venv\\Scripts\\python.exe scripts\\create_admin.py \\
        --email admin@lysauto.it --nome "Luca Pietroni" --password "..." --yes
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lys_workflow_hub.config import get_settings  # noqa: E402
from lys_workflow_hub.core.utenti_repository import UtentiRepository  # noqa: E402


def _prompt_password() -> str:
    while True:
        pw1 = getpass.getpass("Password (min 8 caratteri): ")
        if len(pw1) < 8:
            print("Troppo corta, riprova.")
            continue
        pw2 = getpass.getpass("Ripeti password: ")
        if pw1 != pw2:
            print("Le due password non coincidono, riprova.")
            continue
        return pw1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="Email dell'admin (chiede interattivamente se omessa)")
    parser.add_argument("--nome", default="", help="Nome visualizzato")
    parser.add_argument("--password", help="Password in chiaro (SOLO per uso non interattivo)")
    parser.add_argument("--yes", action="store_true", help="Non chiedere conferma se esiste già")
    args = parser.parse_args()

    settings = get_settings()
    repo = UtentiRepository(db_path=settings.app_db_path)

    email = args.email or input("Email admin: ").strip()
    if not email or "@" not in email:
        print("Email non valida.", file=sys.stderr)
        sys.exit(1)

    esistente = repo.get_by_email(email)
    if esistente is not None and not args.yes:
        risposta = input(
            f"Utente '{email}' già esistente (ruolo={esistente.ruolo}). "
            "Reimposto la password e lo promuovo ad admin? [s/N] "
        ).strip().lower()
        if risposta != "s":
            print("Annullato.")
            return

    # Un solo criterio per decidere se siamo in modalità scriptata (entrambi
    # --email e --password passati da CLI): usato sia per il nome sia per la
    # password, cosi' i due campi non possono finire su rami diversi.
    non_interattivo = bool(args.email and args.password)
    if args.nome:
        nome = args.nome
    elif non_interattivo:
        nome = ""
    else:
        nome = input("Nome (opzionale): ").strip()

    password = args.password or _prompt_password()

    try:
        if esistente is not None:
            repo.set_password(esistente.id, password)
            if esistente.ruolo != "admin":
                repo.set_ruolo(esistente.id, "admin")
            if nome:
                repo.set_nome(esistente.id, nome)
            print(f"Utente '{email}' aggiornato: password reimpostata, ruolo=admin.")
        else:
            repo.create(email=email, password=password, nome=nome, ruolo="admin")
            print(f"Admin '{email}' creato con successo.")
    except ValueError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
