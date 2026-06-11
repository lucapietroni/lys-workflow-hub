"""Orchestratore dell'invio PEC per il Workflow B (vandalismo).

Mette insieme i mattoni di M2 e M2-bis:

  1. Costruisce il MIME (subject + body + allegati) tramite `pec_mailer.build_message`.
  2. **Prima** dell'invio archivia il file `.eml` su filesystem (così non si perde
     nemmeno se il network crasha).
  3. Esegue l'invio reale (o dry-run) tramite `pec_mailer.send_message`.
  4. Registra il risultato nel DB `pec_inviate` (anche in caso di errore: serve
     l'audit trail).

L'orchestratore è puro Python: tutta la configurazione viene passata
esplicitamente. Le route web costruiscono i parametri leggendo Settings.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lys_workflow_hub.core.pec_log_repository import (
    ESITO_DRY_RUN,
    ESITO_KO,
    ESITO_OK,
    PecInviata,
    PecLogRepository,
)
from lys_workflow_hub.integrations.pec_mailer import (
    BuiltMessage,
    SendResult,
    build_message,
    salva_in_posta_inviata,
    send_message,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.allegati import (
    Allegato,
)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Modello: parametri di invio
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParametriInvio:
    """Tutto ciò che serve per costruire e spedire una PEC vandalismo.

    Si costruisce esplicitamente dalla route (a partire da Settings +
    RichiestaVandalismoData + lista Allegato selezionati).
    """

    numero_pratica: int
    compagnia_id: int | None
    compagnia_nome: str

    sender_email: str
    sender_display: str
    reply_to: str

    recipient_email: str

    subject: str
    body: str
    allegati: list[Allegato]

    # SMTP
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str

    # Operative
    dry_run: bool
    archivio_pec_root: Path  # cartella centrale dove salvare i .eml

    def stima_dimensione_bytes(self) -> int:
        """Stima rapida della dimensione totale del messaggio."""
        n = len((self.body or "").encode("utf-8"))
        for a in self.allegati:
            n += int(a.dimensione_bytes)
        return n


# --------------------------------------------------------------------------- #
#  Archiviazione .eml
# --------------------------------------------------------------------------- #


def _slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
    s = s.strip("_") or "x"
    return s[:max_len]


def _archivia_eml(
    *,
    archivio_root: Path,
    numero_pratica: int,
    compagnia_nome: str,
    message_id: str,
    eml_bytes: bytes,
    now: datetime,
) -> Path:
    """Salva il .eml nell'archivio centrale partizionato per anno.

    Nome file deterministico: `<YYYYMMDD-HHMMSS>_<numpra>_<compagnia>.eml`.
    """
    anno_dir = archivio_root / str(now.year)
    anno_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    fname = f"{timestamp}_{int(numero_pratica)}_{_slug(compagnia_nome)}.eml"
    target = anno_dir / fname
    if target.exists():
        # In caso di collisione (es. doppio invio nello stesso secondo) aggiungiamo un suffisso.
        suffix = _slug(message_id, max_len=8)
        target = anno_dir / f"{target.stem}_{suffix}.eml"
    target.write_bytes(eml_bytes)
    return target


# --------------------------------------------------------------------------- #
#  Orchestrazione
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EsitoInvio:
    """Esito completo dell'orchestrazione (utile alla route per il render)."""

    ok: bool
    dry_run: bool
    record: PecInviata
    error: str = ""


def invia(
    params: ParametriInvio,
    *,
    repo: PecLogRepository,
    now: datetime | None = None,
) -> EsitoInvio:
    """Costruisce, archivia, invia e registra una PEC vandalismo.

    Strategia: prima si costruisce il MIME, poi si tenta l'archiviazione del
    .eml su filesystem (no rischio di perdere il messaggio se SMTP esplode),
    poi si tenta l'invio reale (o dry-run). In ogni ramo si registra un
    record in `pec_inviate`.
    """
    when = now or datetime.now()

    # 1) Build MIME.
    try:
        built: BuiltMessage = build_message(
            sender_email=params.sender_email,
            sender_display=params.sender_display,
            recipient_email=params.recipient_email,
            subject=params.subject,
            body_text=params.body,
            attachments=[a.path for a in params.allegati],
            reply_to=params.reply_to,
        )
    except (ValueError, FileNotFoundError) as exc:
        # Registriamo il fallimento per audit (senza file .eml).
        record = repo.log(
            numero_pratica=params.numero_pratica,
            compagnia_id=params.compagnia_id,
            compagnia_nome=params.compagnia_nome,
            destinatario_pec=params.recipient_email,
            mittente_pec=params.sender_email,
            oggetto=params.subject,
            body=params.body,
            allegati=[a.nome_file for a in params.allegati],
            path_eml="",
            message_id="",
            esito=ESITO_KO,
            errore=f"Costruzione MIME fallita: {exc}",
            data_invio=when,
        )
        return EsitoInvio(ok=False, dry_run=False, record=record, error=str(exc))

    # 2) Archiviazione filesystem (prima dell'invio: no perdita su crash).
    try:
        eml_path = _archivia_eml(
            archivio_root=Path(params.archivio_pec_root),
            numero_pratica=params.numero_pratica,
            compagnia_nome=params.compagnia_nome,
            message_id=built.message_id,
            eml_bytes=built.eml_bytes,
            now=when,
        )
    except OSError as exc:
        record = repo.log(
            numero_pratica=params.numero_pratica,
            compagnia_id=params.compagnia_id,
            compagnia_nome=params.compagnia_nome,
            destinatario_pec=params.recipient_email,
            mittente_pec=params.sender_email,
            oggetto=params.subject,
            body=params.body,
            allegati=[a.nome_file for a in params.allegati],
            path_eml="",
            message_id=built.message_id,
            esito=ESITO_KO,
            errore=f"Archiviazione .eml fallita: {exc}",
            data_invio=when,
        )
        return EsitoInvio(ok=False, dry_run=False, record=record, error=str(exc))

    # 3) Invio reale (o dry-run).
    result: SendResult = send_message(
        built,
        smtp_host=params.smtp_host,
        smtp_port=params.smtp_port,
        smtp_user=params.smtp_user,
        smtp_password=params.smtp_password,
        sender_email=params.sender_email,
        recipient_email=params.recipient_email,
        dry_run=params.dry_run,
    )

    esito = ESITO_DRY_RUN if result.dry_run else (ESITO_OK if result.ok else ESITO_KO)

    # 4) Log nel DB.
    record = repo.log(
        numero_pratica=params.numero_pratica,
        compagnia_id=params.compagnia_id,
        compagnia_nome=params.compagnia_nome,
        destinatario_pec=params.recipient_email,
        mittente_pec=params.sender_email,
        oggetto=params.subject,
        body=params.body,
        allegati=[a.nome_file for a in params.allegati],
        path_eml=str(eml_path),
        message_id=built.message_id,
        esito=esito,
        errore=result.error or "",
        data_invio=when,
    )

    return EsitoInvio(
        ok=result.ok,
        dry_run=result.dry_run,
        record=record,
        error=result.error or "",
    )


# --------------------------------------------------------------------------- #
#  Invio email ordinaria (SMTP normale, non PEC)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EsitoEmailOrdinaria:
    ok: bool
    error: str = ""


def invia_email_ordinaria(
    *,
    pec_id: int,
    email_destinatario: str,
    subject: str,
    body: str,
    allegati_paths: list[Path],
    sender_email: str,
    sender_display: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    dry_run: bool,
    repo: PecLogRepository,
    imap_host: str = "",
    imap_port: int = 993,
    imap_user: str = "",
    imap_password: str = "",
) -> EsitoEmailOrdinaria:
    """Invia il corpo della PEC via email ordinaria e aggiorna il record DB.

    Se imap_host/imap_user sono valorizzati, dopo l'invio tenta IMAP APPEND
    alla cartella Posta inviata (non fatale: loggato ma non blocca).
    """
    try:
        built: BuiltMessage = build_message(
            sender_email=sender_email,
            sender_display=sender_display,
            recipient_email=email_destinatario,
            subject=subject,
            body_text=body,
            attachments=allegati_paths,
            reply_to="",
        )
    except (ValueError, FileNotFoundError) as exc:
        logger.error("invia_email_ordinaria: build_message fallita: %s", exc)
        repo.aggiorna_email_esito(pec_id, email_destinatario, ESITO_KO)
        return EsitoEmailOrdinaria(ok=False, error=str(exc))

    result: SendResult = send_message(
        built,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        sender_email=sender_email,
        recipient_email=email_destinatario,
        dry_run=dry_run,
    )
    esito = ESITO_DRY_RUN if result.dry_run else (ESITO_OK if result.ok else ESITO_KO)
    repo.aggiorna_email_esito(pec_id, email_destinatario, esito)
    logger.info(
        "invia_email_ordinaria: pec_id=%s → %s esito=%s",
        pec_id, email_destinatario, esito,
    )

    if result.ok and not dry_run and imap_host and imap_user:
        try:
            salva_in_posta_inviata(
                built.eml_bytes,
                imap_host=imap_host,
                imap_port=imap_port,
                imap_user=imap_user,
                imap_password=imap_password,
            )
            logger.info("invia_email_ordinaria: IMAP APPEND ok, pec_id=%s", pec_id)
        except Exception as exc:
            logger.warning("invia_email_ordinaria: IMAP APPEND fallito (non fatale): %s", exc)

    return EsitoEmailOrdinaria(ok=result.ok or result.dry_run, error=result.error or "")
