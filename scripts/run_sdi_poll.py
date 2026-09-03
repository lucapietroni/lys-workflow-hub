"""Ciclo polling SDI per la contabilità gestionale (Fase 3).

Gemello di ``run_polling.py`` (Workflow C). Un ciclo singolo:

  1. Importa gli XML delle fatture attive generati da WinCar
     (``SDI_WINCAR_ATTIVE_DIR``) → righe ``contabilita_fattura`` (da_inviare).
  2. Trasmette allo SDI le attive pendenti → stato ``inviata`` + movimento
     proposto in entrata (salvo ``SDI_INVIO_DISABILITATO=true``).
  3. Scarica dallo SDI le fatture passive ricevute → righe
     ``contabilita_fattura`` (passiva) + movimento proposto in uscita, da
     smistare (coda in Fase 4).
  4. Push ntfy di riepilogo se c'è qualcosa di nuovo o uno scarto.

Da schedulare in Task Scheduler 1x/giorno. Lock file dedicato per evitare
esecuzioni sovrapposte (indipendente da quello di ``run_polling.py``).

Uso:

    cd C:\\LYSApp\\lys-workflow-hub
    .venv\\Scripts\\python.exe scripts\\run_sdi_poll.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", buffering=1, encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", buffering=1, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from lys_workflow_hub.config import get_settings  # noqa: E402
from lys_workflow_hub.core.contabilita_categoria_repository import (  # noqa: E402
    ContabilitaCategoriaRepository,
)
from lys_workflow_hub.core.contabilita_fattura_repository import (  # noqa: E402
    ContabilitaFatturaRepository,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (  # noqa: E402
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.core.utenti_repository import UtentiRepository  # noqa: E402
from lys_workflow_hub.core.wincar_fatture_repository import (  # noqa: E402
    WinCarFattureRepository,
)
from lys_workflow_hub.integrations.notifier import (  # noqa: E402
    notify_fcm_nuova_attivita,
    notify_push_nuova_attivita,
)
from lys_workflow_hub.integrations.sdi import build_sdi_client  # noqa: E402
from lys_workflow_hub.workflows.contabilita.sdi_import import (  # noqa: E402
    InvioSummary,
    importa_attive_da_dir,
    invia_attive_pendenti,
    sincronizza_passive,
)
from run_polling import PollingLock, _setup_logging  # noqa: E402


def _parse_since(value: str) -> date | None:
    if not (value or "").strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        logging.getLogger("sdi").warning("SDI_FETCH_SINCE non valido (%r), ignorato.", value)
        return None


def run_once() -> int:
    settings = get_settings()

    log_path = Path(settings.app_log_path).with_name("sdi_poll.log")
    _setup_logging(log_path, level_name=settings.app_log_level)
    log = logging.getLogger("sdi")

    lock_path = Path(settings.app_log_path).parent / "sdi_poll.lock"
    try:
        with PollingLock(lock_path, stale_minutes=60):
            log.info("=== Inizio ciclo SDI ===")

            fattura_repo = ContabilitaFatturaRepository(db_path=settings.app_db_path)
            movimento_repo = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
            client = build_sdi_client(settings)
            piva = settings.sdi_piva_azienda
            archivio = Path(settings.app_archivio_fatture)

            # Import attive: come 'storico' (le invia WinCar / il
            # commercialista). Anno corrente + cutoff .env. Categoria fissa
            # "Riparazioni carrozzeria" e legame pratica letto da wcFatture.mdb.
            cat_repo = ContabilitaCategoriaRepository(db_path=settings.app_db_path)
            _cats = {c.nome.strip().lower(): c.id for c in cat_repo.list_all()}
            imp = importa_attive_da_dir(
                Path(settings.sdi_wincar_attive_dir),
                piva_azienda=piva,
                fattura_repo=fattura_repo,
                movimento_repo=movimento_repo,
                wincar_fatture_repo=WinCarFattureRepository.from_settings(settings),
                anno=date.today().year,
                since=_parse_since(settings.sdi_attive_import_since),
                come_storico=True,
                categoria_id=_cats.get("riparazioni carrozzeria"),
                categoria_nc_id=_cats.get("nota di credito"),
                archivio_dir=archivio,
            )
            log.info(
                "Attive import: esaminati=%d nuove=%d (collegate pratica=%d) "
                "duplicate=%d fuori_periodo=%d errori=%d",
                imp.esaminati, imp.nuove, imp.collegate_pratica,
                imp.duplicate, imp.fuori_periodo, len(imp.errori),
            )
            for e in imp.errori:
                log.warning("import attive: %s", e)

            # Invio attive allo SDI: SOLO se esplicitamente abilitato
            # (SDI_INVIO_ATTIVE_AUTO). Default: non inviamo nulla in automatico,
            # l'invio è un'azione manuale da /contabilita/fatture.
            inv = InvioSummary()
            if settings.sdi_invio_attive_auto:
                inv = invia_attive_pendenti(
                    client=client,
                    fattura_repo=fattura_repo,
                    movimento_repo=movimento_repo,
                    disabilitato=bool(settings.sdi_invio_disabilitato),
                )
                log.info(
                    "Attive invio: tentate=%d inviate=%d scartate=%d movimenti=%d errori=%d",
                    inv.tentate, inv.inviate, inv.scartate, inv.movimenti_creati, len(inv.errori),
                )
                for e in inv.errori:
                    log.warning("invio attive: %s", e)
            else:
                log.info("Attive invio: disattivato (SDI_INVIO_ATTIVE_AUTO=false).")

            sync = sincronizza_passive(
                client=client,
                fattura_repo=fattura_repo,
                movimento_repo=movimento_repo,
                piva_azienda=piva,
                since=_parse_since(settings.sdi_fetch_since),
                archivio_dir=archivio,
            )
            log.info(
                "Passive sync: ricevute=%d nuove=%d duplicate=%d movimenti=%d errori=%d",
                sync.ricevute, sync.nuove, sync.duplicate, sync.movimenti_creati, len(sync.errori),
            )
            for e in sync.errori:
                log.warning("sync passive: %s", e)

            _notifica(settings, imp, inv, sync, log)

            log.info("=== Fine ciclo SDI ===")
            return 0
    except RuntimeError as exc:
        logging.getLogger("sdi").warning(str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("sdi").exception("Errore fatale nel ciclo SDI: %s", exc)
        return 1


def _notifica(settings, imp, inv, sync, log) -> None:
    if settings.notify_disabled or not settings.ntfy_topic:
        return
    if not (imp.nuove or inv.inviate or inv.scartate or sync.nuove):
        return
    righe = []
    if imp.nuove:
        righe.append(f"{imp.nuove} fatture attive importate")
    if inv.inviate:
        righe.append(f"{inv.inviate} inviate allo SDI")
    if inv.scartate:
        righe.append(f"⚠️ {inv.scartate} scartate dallo SDI")
    if sync.nuove:
        righe.append(f"{sync.nuove} fatture passive ricevute (da smistare)")
    titolo = "Contabilità — ciclo SDI"
    messaggio = "\n".join(righe)
    try:
        notify_push_nuova_attivita(
            ntfy_server=settings.ntfy_server,
            ntfy_topic=settings.ntfy_topic,
            titolo=titolo,
            messaggio=messaggio,
            click_url=settings.public_url("/contabilita/fatture"),
            disabled=settings.notify_disabled,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Push ntfy riepilogo SDI fallito: %s", exc)
    # FCM all'admin loggato in app / browser (stesso pattern di run_polling.py).
    try:
        utenti_repo = UtentiRepository(db_path=settings.app_db_path)
        for u in utenti_repo.list_all():
            if not (u.is_admin and u.attivo):
                continue
            for token in (u.fcm_token, u.fcm_token_web):
                if token:
                    notify_fcm_nuova_attivita(
                        fcm_project_id=settings.fcm_project_id,
                        fcm_credentials_path=str(settings.fcm_credentials_path or ""),
                        fcm_token=token,
                        titolo=titolo,
                        messaggio=messaggio,
                        click_path="/contabilita/fatture",
                        disabled=settings.notify_disabled,
                    )
    except Exception as exc:  # noqa: BLE001
        log.warning("Push FCM riepilogo SDI fallito: %s", exc)


if __name__ == "__main__":
    sys.exit(run_once())
