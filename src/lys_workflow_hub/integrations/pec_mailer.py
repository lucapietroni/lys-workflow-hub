"""Costruzione e invio di un messaggio PEC via SMTP/TLS.

Il modulo lavora a due livelli:

  - **build_message**: costruisce un `EmailMessage` completo (subject, body
    plain-text utf-8, allegati base64) e lo serializza in bytes RFC-822.
    Genera anche un `Message-ID` deterministico (RFC 2822 compliant).
    Non apre alcuna connessione di rete.

  - **send_message**: prende i bytes prodotti da `build_message` e li
    consegna via SMTP_SSL (default InfoCert/Legalmail su porta 465).
    In modalità dry-run (parametro `dry_run=True`) NON apre la connessione
    e ritorna un esito simulato.

La separazione tra "costruisco" e "invio" permette di:
  1. Archiviare il .eml prima di tentare l'invio (no perdita su crash).
  2. Testare la costruzione senza mock di smtplib.
  3. Riusare la stessa funzione per la modalità dry-run.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path


logger = logging.getLogger(__name__)


# Limiti consigliati per non incorrere nei controlli antispam delle PEC.
PEC_MAX_TOTAL_BYTES = 30 * 1024 * 1024  # ~30 MB
PEC_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


# --------------------------------------------------------------------------- #
#  Tipi di ritorno
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BuiltMessage:
    """Messaggio pronto: bytes RFC-822 + metadati di servizio."""

    eml_bytes: bytes
    message_id: str
    total_size_bytes: int


@dataclass(frozen=True)
class SendResult:
    """Esito dell'invio SMTP."""

    ok: bool
    message_id: str
    dry_run: bool
    smtp_response: str = ""
    error: str = ""


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _attach_file(msg: EmailMessage, path: Path) -> int:
    """Aggiunge un file al messaggio MIME. Ritorna la dimensione in byte."""
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Allegato non trovato: {path}")
    raw = path.read_bytes()
    if len(raw) > PEC_MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"Allegato troppo grande ({len(raw)} byte > {PEC_MAX_ATTACHMENT_BYTES}): {path.name}"
        )
    # Inferiamo subtype dal suffisso. python-stdlib email.message si occupa del base64 transparently.
    ext = path.suffix.lower().lstrip(".")
    maintype, subtype = _guess_mime(ext)
    msg.add_attachment(raw, maintype=maintype, subtype=subtype, filename=path.name)
    return len(raw)


_MIME_MAP = {
    "pdf": ("application", "pdf"),
    "jpg": ("image", "jpeg"),
    "jpeg": ("image", "jpeg"),
    "png": ("image", "png"),
    "gif": ("image", "gif"),
    "webp": ("image", "webp"),
    "bmp": ("image", "bmp"),
    "tif": ("image", "tiff"),
    "tiff": ("image", "tiff"),
    "heic": ("image", "heic"),
    "heif": ("image", "heif"),
    "doc": ("application", "msword"),
    "docx": (
        "application",
        "vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "xls": ("application", "vnd.ms-excel"),
    "xlsx": (
        "application",
        "vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "txt": ("text", "plain"),
    "eml": ("message", "rfc822"),
}


def _guess_mime(ext: str) -> tuple[str, str]:
    return _MIME_MAP.get(ext, ("application", "octet-stream"))


# --------------------------------------------------------------------------- #
#  Build
# --------------------------------------------------------------------------- #


def build_message(
    *,
    sender_email: str,
    sender_display: str,
    recipient_email: str,
    subject: str,
    body_text: str,
    attachments: list[Path],
    reply_to: str = "",
    message_id_domain: str = "lysauto.local",
) -> BuiltMessage:
    """Costruisce un EmailMessage RFC-822 con corpo testuale e allegati.

    - `sender_email`: l'indirizzo PEC mittente (es. la PEC della carrozzeria).
      Deve corrispondere al parametro SMTP_USER autenticato sul server PEC.
    - `sender_display`: display name (es. "Carrozzeria LYS Auto srl").
    - `reply_to`: indirizzo opzionale a cui inviare risposte non-PEC
      (es. email ordinaria della carrozzeria).
    - `message_id_domain`: dominio usato per il Message-ID generato.
    """
    if not sender_email or "@" not in sender_email:
        raise ValueError(f"Mittente PEC non valido: {sender_email!r}")
    if not recipient_email or "@" not in recipient_email:
        raise ValueError(f"Destinatario PEC non valido: {recipient_email!r}")
    if not subject:
        raise ValueError("Oggetto vuoto.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_display or sender_email, sender_email))
    msg["To"] = recipient_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Date"] = formatdate(localtime=True)
    message_id = make_msgid(idstring=uuid.uuid4().hex[:12], domain=message_id_domain)
    msg["Message-ID"] = message_id

    msg.set_content(body_text or "", subtype="plain", charset="utf-8")

    total = len((body_text or "").encode("utf-8"))
    for path in attachments:
        total += _attach_file(msg, Path(path))

    if total > PEC_MAX_TOTAL_BYTES:
        raise ValueError(
            f"Messaggio troppo grande: {total} byte > {PEC_MAX_TOTAL_BYTES} "
            "(limite tipico per PEC). Riduci numero/dimensione allegati."
        )

    return BuiltMessage(
        eml_bytes=bytes(msg),
        message_id=message_id,
        total_size_bytes=total,
    )


# --------------------------------------------------------------------------- #
#  Send
# --------------------------------------------------------------------------- #


def send_message(
    built: BuiltMessage,
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    sender_email: str,
    recipient_email: str,
    dry_run: bool = False,
    timeout_seconds: int = 30,
) -> SendResult:
    """Spedisce il messaggio già costruito.

    In modalità `dry_run=True` la funzione restituisce `SendResult(ok=True,
    dry_run=True)` senza aprire alcuna connessione. Il file `.eml` resta
    comunque utile per audit e va archiviato a parte dal chiamante.

    Provider di default: InfoCert/Legalmail (sendm.cert.legalmail.it:465 SSL).
    """
    if dry_run:
        logger.info(
            "PEC dry-run: skip invio reale. To=%s, Subject in Message-ID=%s",
            recipient_email,
            built.message_id,
        )
        return SendResult(
            ok=True,
            message_id=built.message_id,
            dry_run=True,
            smtp_response="DRY_RUN (no SMTP connection opened)",
        )

    if not smtp_host or not smtp_port:
        return SendResult(
            ok=False,
            message_id=built.message_id,
            dry_run=False,
            error="Server SMTP non configurato in .env.",
        )
    if not smtp_user or not smtp_password:
        return SendResult(
            ok=False,
            message_id=built.message_id,
            dry_run=False,
            error="Credenziali SMTP mancanti in .env.",
        )

    context = ssl.create_default_context()

    try:
        if smtp_port == 465:
            # SSL implicito (tipico delle PEC InfoCert/Aruba).
            with smtplib.SMTP_SSL(
                host=smtp_host, port=smtp_port,
                context=context, timeout=timeout_seconds,
            ) as smtp:
                smtp.login(smtp_user, smtp_password)
                smtp.sendmail(sender_email, [recipient_email], built.eml_bytes)
                resp = "Inviata via SMTP_SSL"
        else:
            # STARTTLS (porta 587 o altre).
            with smtplib.SMTP(
                host=smtp_host, port=smtp_port, timeout=timeout_seconds,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(smtp_user, smtp_password)
                smtp.sendmail(sender_email, [recipient_email], built.eml_bytes)
                resp = "Inviata via STARTTLS"
    except smtplib.SMTPException as exc:
        logger.exception("Errore SMTP durante invio PEC a %s", recipient_email)
        return SendResult(
            ok=False,
            message_id=built.message_id,
            dry_run=False,
            error=f"SMTP error: {exc}",
        )
    except OSError as exc:
        logger.exception("Errore di rete durante invio PEC a %s", recipient_email)
        return SendResult(
            ok=False,
            message_id=built.message_id,
            dry_run=False,
            error=f"Errore di rete: {exc}",
        )

    return SendResult(
        ok=True,
        message_id=built.message_id,
        dry_run=False,
        smtp_response=resp,
    )
