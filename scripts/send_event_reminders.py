"""Reminder "il giorno prima" per gli eventi di calendario condiviso
(v3.0 fase 5, parte B).

Esegue un ciclo singolo:

  1. Legge gli eventi di calendario (`pratica_eventi`) con data = domani.
  2. Per ognuno, se non già notificato (tabella dedup
     `pratica_eventi_reminder`), manda:
     - push all'admin (topic ntfy.sh globale, `.env`)
     - email/push ai collaboratori esterni assegnati alla pratica,
       rispettando le preferenze self-service di ciascuno
       (`notify_email_enabled`/`notify_push_enabled`/`ntfy_topic`)
  3. Segna il reminder come inviato, cosi' una seconda esecuzione nello
     stesso giorno (es. riavvio Task Scheduler) non rispedisce nulla.

Garanzia "at-least-once", non "exactly-once": se il processo crasha (o il
PC si spegne) esattamente tra l'invio delle notifiche e la scrittura del
dedup, un run successivo nello stesso giorno rinotifica l'evento — scelta
deliberata (un reminder duplicato raro è preferibile a uno mai arrivato).
Un errore su un singolo evento (es. `sqlite3.OperationalError` transitorio
sul DB condiviso con l'app FastAPI in esecuzione) NON deve mai bloccare gli
eventi successivi dello stesso ciclo: `list_domani()` fa un match di data
ESATTO, quindi un evento saltato oggi non verrebbe più ritentato domani.

Pensato per essere schedulato in Task Scheduler una volta al giorno (es.
07:00, prima dell'apertura). Usa lo stesso pattern di
`scripts/run_polling.py`: lock file, logging su file con rotazione, mai
un'eccezione non gestita fa fallire silenziosamente lo script.

Uso da riga di comando:

    cd C:\\LYSApp\\lys-workflow-hub
    .venv\\Scripts\\python.exe scripts\\send_event_reminders.py
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

# Fix pythonw.exe (vedi main.py).
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", buffering=1, encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", buffering=1, encoding="utf-8")

# Aggiungi src al PYTHONPATH se lanciato direttamente.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lys_workflow_hub.config import get_settings  # noqa: E402
from lys_workflow_hub.core.pratica_assegnazioni_repository import (  # noqa: E402
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.pratica_eventi_repository import (  # noqa: E402
    Evento,
    PraticaEventiRepository,
)
from lys_workflow_hub.core.utenti_repository import UtentiRepository  # noqa: E402
from lys_workflow_hub.integrations.notifier import (  # noqa: E402
    notify_esterno_nuova_attivita,
    notify_push_nuova_attivita,
)


def _setup_logging(log_path: Path, level_name: str = "INFO") -> None:
    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        filename=str(log_path), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setFormatter(fmt)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(fh)
    root.addHandler(sh)
    root.setLevel(level)


class _Lock:
    """Stesso pattern di `PollingLock` in `run_polling.py`, duplicato qui
    (non importato) per non accoppiare i due script schedulati fra loro —
    ognuno resta eseguibile ed testabile in isolamento."""

    def __init__(self, path: Path, stale_minutes: int = 30) -> None:
        self.path = path
        self.stale_minutes = stale_minutes

    def __enter__(self) -> "_Lock":
        if self.path.exists():
            age_min = (time.time() - self.path.stat().st_mtime) / 60
            if age_min < self.stale_minutes:
                raise RuntimeError(
                    f"Reminder già in esecuzione (lock attivo da {age_min:.1f} min): {self.path}"
                )
            logging.warning("Lock obsoleto (%.1f min) → lo riprendo.", age_min)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _notifica_evento(
    evento: Evento,
    *,
    settings,
    utenti_repo: UtentiRepository,
    assegnazioni_repo: PraticaAssegnazioniRepository,
    log: logging.Logger,
) -> None:
    data_label = evento.data_evento.strftime("%d/%m/%Y") if evento.data_evento else "domani"
    titolo_push = f"Domani: {evento.titolo}"
    messaggio = f"Pratica {evento.pratica_numero} — {evento.titolo} ({data_label})"

    # Admin: push sul topic globale (stesso canale degli alert PEC).
    try:
        notify_push_nuova_attivita(
            ntfy_server=settings.ntfy_server,
            ntfy_topic=settings.ntfy_topic,
            titolo=titolo_push,
            messaggio=messaggio,
            click_url=settings.public_url(f"/pratiche/{evento.pratica_numero}#calendario"),
            disabled=bool(settings.notify_disabled),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Reminder push admin fallito (evento %s): %s", evento.id, exc)

    # Esterni assegnati alla pratica, secondo le loro preferenze.
    try:
        assegnati_ids = set(
            assegnazioni_repo.list_utente_ids_per_pratica(evento.pratica_numero)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Impossibile leggere assegnazioni per %s: %s", evento.pratica_numero, exc)
        return
    if not assegnati_ids:
        return

    for u in utenti_repo.list_all():
        if u.id not in assegnati_ids or not u.attivo:
            continue
        if u.notify_email_enabled and u.email:
            try:
                notify_esterno_nuova_attivita(
                    smtp_host=settings.smtp_host,
                    smtp_port=settings.smtp_port,
                    smtp_user=settings.smtp_user,
                    smtp_password=settings.smtp_password,
                    smtp_sender=settings.smtp_from,
                    recipient=u.email,
                    subject=f"[LYS Hub] {titolo_push}",
                    body_text=(
                        f"{messaggio}\n\n"
                        f"Apri la pratica: "
                        f"{settings.public_url(f'/portale/pratiche/{evento.pratica_numero}#calendario')}"
                    ),
                    smtp_tls=settings.smtp_tls,
                    disabled=bool(settings.notify_disabled),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Reminder email a %s fallito (evento %s): %s", u.email, evento.id, exc)
        if u.notify_push_enabled and u.ntfy_topic:
            try:
                notify_push_nuova_attivita(
                    ntfy_server=settings.ntfy_server,
                    ntfy_topic=u.ntfy_topic,
                    titolo=titolo_push,
                    messaggio=messaggio,
                    click_url=settings.public_url(
                        f"/portale/pratiche/{evento.pratica_numero}#calendario"
                    ),
                    disabled=bool(settings.notify_disabled),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Reminder push a utente %s fallito (evento %s): %s", u.id, evento.id, exc)


def run_once() -> int:
    """Esegue un ciclo singolo. Restituisce un exit code (0=OK, 2=lock attivo,
    1=errore generico)."""
    settings = get_settings()

    log_path = Path(settings.app_log_path).with_name("event_reminders.log")
    _setup_logging(log_path, level_name=settings.app_log_level)
    log = logging.getLogger("event_reminders")

    lock_path = Path(settings.app_log_path).parent / "event_reminders.lock"
    try:
        with _Lock(lock_path):
            log.info("=== Inizio reminder eventi ===")

            eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
            assegnazioni_repo = PraticaAssegnazioniRepository(db_path=settings.app_db_path)
            utenti_repo = UtentiRepository(
                db_path=settings.app_db_path,
                max_attempts=settings.login_max_attempts,
                lockout_minutes=settings.login_lockout_minutes,
            )

            eventi = eventi_repo.list_domani()
            log.info("Eventi domani: %d", len(eventi))

            inviati = 0
            for evento in eventi:
                if eventi_repo.reminder_gia_inviato(evento.id):
                    log.info("Evento %s (pratica %s): reminder già inviato, skip.",
                             evento.id, evento.pratica_numero)
                    continue
                # Isolamento per-evento: `list_domani()` fa un match di data
                # ESATTO ("domani"), quindi un evento saltato oggi per un
                # errore non pianificato (es. sqlite3.OperationalError sul DB
                # condiviso con l'app FastAPI in esecuzione) non verrebbe mai
                # più ritentato — domani sarà "oggi", non più "domani". Un
                # errore su un evento non deve quindi mai far perdere anche
                # tutti gli eventi successivi dello stesso ciclo.
                try:
                    _notifica_evento(
                        evento,
                        settings=settings,
                        utenti_repo=utenti_repo,
                        assegnazioni_repo=assegnazioni_repo,
                        log=log,
                    )
                    eventi_repo.segna_reminder_inviato(evento.id)
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "Evento %s (pratica %s): reminder fallito, salto al prossimo: %s",
                        evento.id, evento.pratica_numero, exc,
                    )
                    continue
                inviati += 1
                log.info("Evento %s (pratica %s): reminder inviato.",
                         evento.id, evento.pratica_numero)

            log.info("=== Fine reminder eventi: %d inviati / %d totali ===", inviati, len(eventi))
            return 0
    except RuntimeError as exc:
        logging.getLogger("event_reminders").warning(str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("event_reminders").exception("Errore fatale: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run_once())
