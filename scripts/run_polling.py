"""Polling script per il Workflow C (lettura risposte assicurazioni, M3).

Esegue un ciclo singolo:

  1. Fetch IMAP delle nuove mail dalla casella PEC (e opzionalmente da quella
     email ordinaria).
  2. Per ogni nuova mail:
     a. Tenta il matching alla PEC inviata "padre" (header + euristica).
     b. Classifica con AI Anthropic (o skip se ai_disabled).
     c. Salva la classificazione nel DB `mail_classificate`.
  3. A fine ciclo, manda notifiche (push ntfy.sh + email riassuntiva).

Pensato per essere schedulato in Task Scheduler 2 volte al giorno (es. 09:00 e
17:00). Usa un lock file per evitare esecuzioni sovrapposte.

Uso da riga di comando:

    cd C:\\LYSApp\\lys-workflow-hub
    .venv\\Scripts\\python.exe scripts\\run_polling.py
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
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
from lys_workflow_hub.core.compagnie_repository import CompagnieRepository  # noqa: E402
from lys_workflow_hub.core.draft_repository import DraftRepository  # noqa: E402
from lys_workflow_hub.core.mail_in_repository import (  # noqa: E402
    CASELLA_EMAIL,
    CASELLA_PEC,
    CAT_ALTRO,
    MailClassificata,
    MailIn,
    MailRepository,
)
from lys_workflow_hub.core.categoria_policy_repository import (  # noqa: E402
    CategoriaPolicyRepository,
)
from lys_workflow_hub.core.pec_log_repository import PecLogRepository  # noqa: E402
from lys_workflow_hub.core.pratica_stato_repository import (  # noqa: E402
    PraticaStatoRepository,
)
from lys_workflow_hub.core.wincar_repository import WinCarRepository  # noqa: E402
from lys_workflow_hub.integrations.ai_classifier import classify  # noqa: E402
from lys_workflow_hub.integrations.imap_fetcher import ImapFetcher  # noqa: E402
from lys_workflow_hub.integrations.notifier import notify_batch  # noqa: E402
from lys_workflow_hub.workflows.risposte.context_builder import (  # noqa: E402
    build_scaffold_context,
)
from lys_workflow_hub.workflows.risposte.draft_service import (  # noqa: E402
    crea_bozza_se_serve,
)
from lys_workflow_hub.workflows.risposte.matcher import match_mail  # noqa: E402


# --------------------------------------------------------------------------- #
#  Logging
# --------------------------------------------------------------------------- #


def _setup_logging(log_path: Path, level_name: str = "INFO") -> None:
    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        filename=str(log_path),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
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


# --------------------------------------------------------------------------- #
#  Lock file (evita esecuzioni sovrapposte)
# --------------------------------------------------------------------------- #


class PollingLock:
    """Lock file basato su path. Crea il file all'enter, lo cancella all'exit.
    Se il file esiste già e ha meno di `stale_minutes` minuti, considera il
    lock attivo. Altrimenti lo riprende (presumendo un crash precedente)."""

    def __init__(self, path: Path, stale_minutes: int = 60) -> None:
        self.path = path
        self.stale_minutes = stale_minutes

    def __enter__(self) -> "PollingLock":
        import time
        if self.path.exists():
            try:
                age_min = (time.time() - self.path.stat().st_mtime) / 60
            except OSError:
                age_min = 0
            if age_min < self.stale_minutes:
                raise RuntimeError(
                    f"Polling già in esecuzione (lock attivo da {age_min:.1f} min): {self.path}"
                )
            logging.warning(
                "Lock obsoleto (%.1f min) → lo riprendo.", age_min,
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
#  Pipeline
# --------------------------------------------------------------------------- #


def _parse_since(value: str):
    """Converte 'YYYY-MM-DD' (o vuoto) in date | None per il fetcher."""
    from datetime import date as _date
    if not (value or "").strip():
        return None
    try:
        return _date.fromisoformat(value.strip())
    except ValueError:
        logging.getLogger("polling").warning(
            "MAIL_FETCH_SINCE non valido (%r), ignorato.", value
        )
        return None


def _fetch_caselle(
    *,
    mail_repo: MailRepository,
    archivio_root: Path,
    settings,
) -> list[int]:
    """Scarica nuove mail da PEC + email ordinaria. Restituisce gli id mail_in
    nuovi inseriti nel DB."""
    nuovi_id: list[int] = []
    log = logging.getLogger("polling")

    since_date = _parse_since(settings.mail_fetch_since)
    if since_date is not None:
        log.info("Filtro IMAP SINCE attivo: %s", since_date.isoformat())

    # PEC
    if settings.pec_user and settings.pec_password:
        fetcher_pec = ImapFetcher(
            host=settings.pec_imap_host,
            port=settings.pec_imap_port,
            user=settings.pec_user,
            password=settings.pec_password,
        )
        result = fetcher_pec.fetch_into(
            mail_repo,
            casella=CASELLA_PEC,
            archivio_root=archivio_root,
            since_date=since_date,
            pdf_extract_enabled=bool(settings.pdf_extract_enabled),
            pdf_extract_min_body_len=int(settings.pdf_extract_min_body_len),
        )
        log.info(
            "PEC fetch: scaricati=%d duplicati=%d errori=%d",
            result.scaricati, result.duplicati, result.errori,
        )
        nuovi_id.extend(result.nuovi_id)
    else:
        log.info("PEC: credenziali non configurate, skip.")

    # Email ordinaria
    if settings.email_user and settings.email_password:
        fetcher_email = ImapFetcher(
            host=settings.email_imap_host,
            port=settings.email_imap_port,
            user=settings.email_user,
            password=settings.email_password,
        )
        result = fetcher_email.fetch_into(
            mail_repo,
            casella=CASELLA_EMAIL,
            archivio_root=archivio_root,
            since_date=since_date,
            pdf_extract_enabled=bool(settings.pdf_extract_enabled),
            pdf_extract_min_body_len=int(settings.pdf_extract_min_body_len),
        )
        log.info(
            "Email fetch: scaricati=%d duplicati=%d errori=%d",
            result.scaricati, result.duplicati, result.errori,
        )
        nuovi_id.extend(result.nuovi_id)
    else:
        log.info("Email: credenziali non configurate, skip.")

    return nuovi_id


_PEC_RECEIPT_SUBJ_PREFIXES = (
    "ACCETTAZIONE:",
    "CONSEGNA:",
    "AVVENUTA CONSEGNA:",
    "MANCATA CONSEGNA:",
    "NON ACCETTAZIONE:",
    "ANOMALIA MESSAGGIO:",
    "ERRORE CONSEGNA:",
    "PRESA IN CARICO:",
)
# NB: "POSTA CERTIFICATA:" da solo NON è una ricevuta. È il prefisso che il
# provider mette sull'oggetto di QUALSIASI messaggio PEC reale in arrivo
# (incapsulato come postacert.eml). Le ricevute tecniche hanno prefissi
# diversi (ACCETTAZIONE, CONSEGNA, ...).


def _is_pec_receipt(mail: MailIn) -> bool:
    """Riconosce le ricevute di sistema generate da InfoCert / gestori PEC.

    Sono inutili da classificare via AI (è solo conferma di consegna o
    accettazione del messaggio, non una risposta dal cessionario) e fanno solo
    spendere budget Anthropic.

    Importante: NON basta vedere `posta-certificata@` nel mittente per
    decidere — anche le PEC REALI in arrivo hanno quel mittente tecnico,
    perché il provider del destinatario incapsula sempre i messaggi PEC.
    Le ricevute si riconoscono inequivocabilmente solo dai PREFISSI
    canonici dell'oggetto (ACCETTAZIONE, CONSEGNA, ...).
    """
    subj_upper = (mail.subject or "").upper()
    return subj_upper.startswith(_PEC_RECEIPT_SUBJ_PREFIXES)


def _classifica_e_logga(
    mail_id: int,
    *,
    mail_repo: MailRepository,
    pec_repo: PecLogRepository,
    settings,
):
    log = logging.getLogger("polling")
    mail = mail_repo.get_mail(mail_id)
    if mail is None:
        log.warning("mail_id %s non trovata", mail_id)
        return None

    # Short-circuit: ricevute PEC di sistema → classifica come "altro" gratis,
    # senza chiamare matcher né AI. Restano in DB per audit, ma non spendono
    # budget e non vengono mostrate in /risposte (perché pec_inviata_id=None).
    if _is_pec_receipt(mail):
        log.info("Mail %s: ricevuta PEC di sistema, skip AI/matcher.", mail.id)
        classif = mail_repo.save_classification(
            mail_in_id=mail.id,
            pec_inviata_id=None,
            pratica_numero=None,
            categoria=CAT_ALTRO,
            confidence=1.0,
            summary="Ricevuta PEC di sistema (consegna/accettazione/anomalia).",
            action_required=False,
            key_facts={},
            ai_model="(skip)",
            ai_cost_eur=0.0,
            match_method="none",
            match_confidence=0.0,
        )
        return (mail, classif)

    # 1) Matching
    match = match_mail(mail, pec_repo)
    log.info(
        "Mail %s: match=%s pratica=%s conf=%.2f",
        mail.id, match.method, match.pratica_numero, match.confidence,
    )

    # Short-circuit: nessuna pratica abbinata → non ha senso classificare
    # con AI (non è una risposta assicurativa che ci riguarda). Salviamo
    # come "altro" a costo zero; l'email di riepilogo filtrerà comunque
    # queste voci.
    if match.pratica_numero is None:
        log.info("Mail %s: no match pratica, skip AI → altro", mail.id)
        classif = mail_repo.save_classification(
            mail_in_id=mail.id,
            pec_inviata_id=None,
            pratica_numero=None,
            categoria=CAT_ALTRO,
            confidence=0.0,
            summary="Nessuna pratica corrispondente trovata.",
            action_required=False,
            key_facts={},
            ai_model="(skip-no-match)",
            ai_cost_eur=0.0,
            match_method=match.method,
            match_confidence=match.confidence,
        )
        return (mail, classif)

    # 2) AI classify
    result = classify(
        subject=mail.subject,
        sender=mail.sender,
        body=mail.body_text,
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        disabled=bool(settings.ai_disabled),
    )
    log.info(
        "Mail %s: categoria=%s conf=%.2f cost=%s EUR",
        mail.id, result.categoria, result.confidence, result.ai_cost_eur,
    )

    # 3) Salva nel DB.
    classif = mail_repo.save_classification(
        mail_in_id=mail.id,
        pec_inviata_id=match.pec_inviata_id,
        pratica_numero=match.pratica_numero,
        categoria=result.categoria,
        confidence=result.confidence,
        summary=result.summary,
        action_required=result.action_required,
        key_facts=result.key_facts,
        ai_model=result.ai_model,
        ai_cost_eur=result.ai_cost_eur,
        match_method=match.method,
        match_confidence=match.confidence,
    )
    return (mail, classif)


def _sla_notify(
    alert,
    *,
    stato_repo: "PraticaStatoRepository",
    ntfy_server: str,
    ntfy_topic: str,
    disabled: bool,
    base_url: str,
) -> None:
    """Invia push SLA per una PEC senza risposta oltre soglia e logga il reminder."""
    log = logging.getLogger("polling")
    try:
        if not disabled and ntfy_topic:
            import urllib.request
            url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}"
            titolo = f"SLA scaduto: pratica {alert.pratica_numero}"
            corpo = (
                f"Nessuna risposta da {alert.compagnia_nome} "
                f"dopo {alert.giorni_attesa} giorni.\n"
                f"PEC inviata il {alert.data_invio.strftime('%d/%m/%Y')}.\n"
                f"{base_url}/pratiche/{alert.pratica_numero}"
            )
            req = urllib.request.Request(
                url,
                data=corpo.encode("utf-8"),
                headers={
                    "Title": titolo.encode("utf-8"),
                    "Priority": "default",
                    "Tags": "warning,hourglass",
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            log.info(
                "Push SLA inviato per pratica %s (pec_id=%s)",
                alert.pratica_numero, alert.pec_inviata_id,
            )
        stato_repo.log_sla_reminder(alert.pec_inviata_id, tipo="push")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Push SLA fallito per pratica %s: %s",
            alert.pratica_numero, exc,
        )


def _genera_bozza_m4(
    mail: MailIn,
    classif: MailClassificata,
    *,
    mail_repo: MailRepository,
    draft_repo: DraftRepository,
    wincar_repo: WinCarRepository | None,
    compagnie_repo: CompagnieRepository,
    settings,
    policy_override: dict[str, str] | None = None,
) -> None:
    """Hook M4: dopo che M3 ha classificato la mail, M4 valuta se serve
    una bozza di risposta e nel caso la crea.

    Non solleva mai: M4 e' un "additional outcome" del polling, non deve
    bloccare il ciclo. Errori vengono solo loggati.

    ``policy_override`` (M5.2): dizionario categoria->policy caricato da
    ``CategoriaPolicyRepository`` una volta per ciclo. Se None, usa il
    dizionario statico hardcoded in ``categorie_policy.py``.
    """
    log = logging.getLogger("polling")
    try:
        ctx_meta = build_scaffold_context(
            pratica_numero=classif.pratica_numero,
            subject_originale=mail.subject,
            wincar_repo=wincar_repo,
            compagnie_repo=compagnie_repo,
            settings=settings,
        )
        draft = crea_bozza_se_serve(
            classif,
            draft_repo=draft_repo,
            mail_repo=mail_repo,
            scaffold_ctx=ctx_meta.context,
            archivio_root=settings.wincar_archivio,
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            ai_disabled=bool(settings.ai_disabled),
            to_address=mail.sender or "",
            policy_override=policy_override,
        )
        if draft is None:
            log.info(
                "M4 mail %s categoria %s: nessuna bozza (policy)",
                mail.id, classif.categoria,
            )
        else:
            log.info(
                "M4 mail %s -> bozza %s creata (pratica=%s, allegati=%d/%d, "
                "ai_cost=%.4f EUR)",
                mail.id, draft.id, draft.pratica_numero,
                len(draft.attachments_included), len(draft.attachments),
                draft.ai_cost_eur,
            )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "M4 hook fallito per mail %s (non blocca il polling): %s",
            mail.id, exc,
        )


def run_once() -> int:
    """Esegue un ciclo singolo. Restituisce un exit code (0=OK, 2=lock attivo,
    1=errore generico)."""
    settings = get_settings()

    log_path = Path(settings.app_log_path).with_name("polling.log")
    _setup_logging(log_path, level_name=settings.app_log_level)
    log = logging.getLogger("polling")

    lock_path = Path(settings.app_log_path).parent / "polling.lock"
    try:
        with PollingLock(lock_path, stale_minutes=60):
            log.info("=== Inizio ciclo polling ===")

            mail_repo = MailRepository(db_path=settings.app_db_path)
            pec_repo = PecLogRepository(db_path=settings.app_db_path)
            # M4 repos: draft + anagrafica + wincar (per ScaffoldContext).
            draft_repo = DraftRepository(db_path=settings.app_db_path)
            compagnie_repo = CompagnieRepository(db_path=settings.app_db_path)
            # M5.2: carica policy bozze da DB una volta per ciclo.
            try:
                _policy_repo = CategoriaPolicyRepository(db_path=settings.app_db_path)
                _policies_db: dict[str, str] | None = _policy_repo.get_all()
                log.info("Policy bozze caricate da DB: %s", _policies_db)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "CategoriaPolicyRepository non inizializzabile, uso policy statiche: %s", exc
                )
                _policies_db = None
            try:
                wincar_repo: WinCarRepository | None = WinCarRepository.from_settings(settings)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "WinCarRepository non inizializzabile (M4 partira' senza dati pratica): %s",
                    exc,
                )
                wincar_repo = None

            # 1) Fetch IMAP
            nuovi_id = _fetch_caselle(
                mail_repo=mail_repo,
                archivio_root=settings.app_archivio_mail_in,
                settings=settings,
            )

            # 2) Anche le mail già in DB ma senza classificazione
            #    (in caso di crash precedente).
            extra = [m.id for m in mail_repo.list_da_classificare(limit=200) if m.id is not None]
            tutti_da_classificare = list(dict.fromkeys(nuovi_id + extra))
            log.info(
                "Da classificare: %d (nuovi=%d, recupero=%d)",
                len(tutti_da_classificare), len(nuovi_id), len(extra),
            )

            classificati: list = []
            for mail_id in tutti_da_classificare:
                try:
                    res = _classifica_e_logga(
                        mail_id,
                        mail_repo=mail_repo,
                        pec_repo=pec_repo,
                        settings=settings,
                    )
                    if res:
                        classificati.append(res)
                        # Hook M4: idempotente e tollerante ai fallimenti.
                        mail_obj, classif_obj = res
                        _genera_bozza_m4(
                            mail_obj, classif_obj,
                            mail_repo=mail_repo,
                            draft_repo=draft_repo,
                            wincar_repo=wincar_repo,
                            compagnie_repo=compagnie_repo,
                            settings=settings,
                            policy_override=_policies_db,
                        )
                        # Hook M5: auto-transizione stato pratica.
                        if classif_obj.pratica_numero is not None:
                            try:
                                stato_repo = PraticaStatoRepository(
                                    db_path=settings.app_db_path
                                )
                                stato_repo.auto_transition(
                                    classif_obj.pratica_numero,
                                    classif_obj.categoria,
                                )
                            except Exception as _exc:  # noqa: BLE001
                                log.warning(
                                    "M5 auto_transition fallita per pratica %s: %s",
                                    classif_obj.pratica_numero, _exc,
                                )
                except Exception as exc:  # noqa: BLE001
                    log.exception("Errore classificando mail %s: %s", mail_id, exc)

            # 3) Notifiche per le mail NUOVE (non per i recuperi).
            nuove_da_notificare = [
                (m, c) for (m, c) in classificati if m.id in nuovi_id
            ]
            log.info("Da notificare: %d", len(nuove_da_notificare))

            base_url = f"http://{settings.app_host}:{settings.app_port}"
            notify_result = notify_batch(
                nuove=nuove_da_notificare,
                ntfy_server=settings.ntfy_server,
                ntfy_topic=settings.ntfy_topic,
                smtp_host=settings.smtp_host,
                smtp_port=settings.smtp_port,
                smtp_user=settings.smtp_user,
                smtp_password=settings.smtp_password,
                smtp_sender=(
                    settings.smtp_from
                    or settings.smtp_user
                    or settings.alert_email
                ),
                smtp_tls=settings.smtp_tls,
                alert_email=settings.alert_email,
                base_url=base_url,
                disabled=bool(settings.notify_disabled),
            )
            log.info(
                "Notifiche: push=%d email=%s errors=%d",
                notify_result.push_sent,
                notify_result.email_sent,
                len(notify_result.errors),
            )
            for err in notify_result.errors:
                log.warning("notifica: %s", err)

            # Alert budget AI.
            cost_mese = mail_repo.ai_cost_mese_corrente()
            log.info("Costo AI mese corrente: %.4f EUR", cost_mese)
            if cost_mese >= settings.ai_budget_alert_eur:
                log.warning(
                    "Soglia di allerta AI raggiunta (%.2f >= %.2f EUR)",
                    cost_mese, settings.ai_budget_alert_eur,
                )

            # Check SLA (M5): PEC senza risposta oltre soglia.
            if settings.sla_giorni_alert > 0:
                try:
                    stato_repo = PraticaStatoRepository(
                        db_path=settings.app_db_path
                    )
                    sla_alerts = stato_repo.lista_sla_alerts(
                        sla_giorni=settings.sla_giorni_alert
                    )
                    pending_sla = [a for a in sla_alerts if not a.already_reminded]
                    log.info(
                        "SLA check: %d alert totali, %d nuovi da notificare",
                        len(sla_alerts), len(pending_sla),
                    )
                    base_url = f"http://{settings.app_host}:{settings.app_port}"
                    for alert in pending_sla:
                        _sla_notify(
                            alert,
                            stato_repo=stato_repo,
                            ntfy_server=settings.ntfy_server,
                            ntfy_topic=settings.ntfy_topic,
                            disabled=bool(settings.notify_disabled),
                            base_url=base_url,
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("SLA check fallito (non blocca): %s", exc)

            log.info("=== Fine ciclo polling ===")
            return 0
    except RuntimeError as exc:
        # lock attivo
        logging.getLogger("polling").warning(str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("polling").exception("Errore fatale in polling: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run_once())
