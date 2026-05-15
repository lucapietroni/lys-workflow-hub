"""Fetcher IMAP per posta in entrata (M3).

Scarica i nuovi messaggi da una casella IMAP (PEC InfoCert su
`mbox.cert.legalmail.it:993` o email ordinaria Tophost su `mail.tophost.it:993`)
e li salva nel repository `MailRepository` + archivia il `.eml` grezzo su
filesystem.

Strategia: **fetch incrementale per UID**. Per ogni casella la repository
ricorda l'UID IMAP più alto già scaricato (`MailRepository.max_uid`). La
prossima chiamata chiede solo gli UID superiori a quello — niente
ridownload di tutto.

Niente delete sulla casella: il server PEC conserva tutto in `INBOX`.

Uso tipico (dentro `run_polling.py`):

    fetcher = ImapFetcher(host, port, user, password)
    result = fetcher.fetch_into(repo, casella=CASELLA_PEC, archivio_root=...)
    print(f"Nuovi messaggi: {result.scaricati}")
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

from lys_workflow_hub.core.mail_in_repository import (
    CASELLA_EMAIL,
    CASELLA_PEC,
    MailIn,
    MailRepository,
)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Risultato
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FetchResult:
    casella: str
    scaricati: int
    duplicati: int
    errori: int
    nuovi_id: list[int]


# --------------------------------------------------------------------------- #
#  Helpers email
# --------------------------------------------------------------------------- #


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str, max_len: int = 80) -> str:
    s = _SAFE_FILENAME_RE.sub("_", (text or "").strip()).strip("_")
    return (s or "msg")[:max_len]


def _parse_received_date(msg: EmailMessage) -> datetime:
    """Estrae la data di invio dal Date: header; fallback a now."""
    raw = msg.get("Date")
    if not raw:
        return datetime.now()
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return datetime.now()
        # Rimuove tzinfo per coerenza con il resto del DB (datetime naive).
        return dt.replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.now()


def _extract_body_text(msg: EmailMessage, limit: int = 8000) -> str:
    """Restituisce il body in plain text. Se l'email è solo HTML, fa un
    decapamento minimale dei tag.

    Per le PEC InfoCert il payload principale del Message-ID destinatario
    è dentro `postacert.eml` attached: quel corpo non lo decifriamo qui,
    leggiamo solo il body diretto del messaggio.
    """
    candidate_text = ""
    candidate_html = ""
    try:
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            charset = body.get_content_charset() or "utf-8"
            payload = body.get_content()
            if isinstance(payload, bytes):
                payload = payload.decode(charset, errors="replace")
            if body.get_content_type() == "text/html":
                candidate_html = payload
            else:
                candidate_text = payload
    except (KeyError, AttributeError):
        # Email non strutturata in modo standard, prova a iterare le parti.
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and not candidate_text:
                try:
                    candidate_text = part.get_content()
                except Exception:  # noqa: BLE001
                    pass
            elif ctype == "text/html" and not candidate_html:
                try:
                    candidate_html = part.get_content()
                except Exception:  # noqa: BLE001
                    pass

    text = candidate_text or _strip_html_minimal(candidate_html)
    return text[:limit]


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WS_RE = re.compile(r"\s+")


def _strip_html_minimal(html: str) -> str:
    """De-tag HTML minimale (non sostituisce un parser, ma basta per il summary)."""
    if not html:
        return ""
    txt = _HTML_TAG_RE.sub(" ", html)
    txt = _HTML_WS_RE.sub(" ", txt).strip()
    return txt


def _has_attachments(msg: EmailMessage) -> bool:
    if not msg.is_multipart():
        return False
    for part in msg.iter_attachments():
        return True
    return False


def _archive_eml(
    archivio_root: Path,
    casella: str,
    received_at: datetime,
    message_id: str,
    subject: str,
    raw: bytes,
) -> Path:
    """Salva il .eml grezzo in archivio_root/<anno>/<casella>/<timestamp>_<slug>.eml."""
    anno_dir = archivio_root / str(received_at.year) / casella
    anno_dir.mkdir(parents=True, exist_ok=True)
    ts = received_at.strftime("%Y%m%d-%H%M%S")
    slug = _slug(subject or "msg", max_len=64)
    fname = f"{ts}_{slug}.eml"
    target = anno_dir / fname
    if target.exists():
        suffix = _slug(message_id, max_len=10)
        target = anno_dir / f"{target.stem}_{suffix}.eml"
    target.write_bytes(raw)
    return target


# --------------------------------------------------------------------------- #
#  Fetcher
# --------------------------------------------------------------------------- #


class ImapFetcher:
    """Wrapper sopra `imaplib.IMAP4_SSL` per fetch incrementale UID-based."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        mailbox: str = "INBOX",
        timeout_seconds: int = 60,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.mailbox = mailbox
        self.timeout_seconds = timeout_seconds

    def _connect(self) -> "imaplib.IMAP4_SSL":
        context = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(
            host=self.host,
            port=self.port,
            ssl_context=context,
            timeout=self.timeout_seconds,
        )
        conn.login(self.user, self.password)
        conn.select(self.mailbox, readonly=True)
        return conn

    def fetch_into(
        self,
        repo: MailRepository,
        *,
        casella: str,
        archivio_root: Path,
        max_messages: int = 200,
    ) -> FetchResult:
        """Scarica le mail con UID > max_uid già visto e le salva nel repository.

        Restituisce un `FetchResult` con statistiche dell'esecuzione.
        """
        if casella not in (CASELLA_PEC, CASELLA_EMAIL):
            raise ValueError(f"Casella non valida: {casella!r}")
        if not self.user or not self.password:
            return FetchResult(
                casella=casella, scaricati=0, duplicati=0, errori=0, nuovi_id=[]
            )

        last_uid = repo.max_uid(casella)
        logger.info(
            "IMAP %s: connecting to %s:%s, last_uid=%s",
            casella, self.host, self.port, last_uid,
        )

        nuovi_id: list[int] = []
        scaricati = duplicati = errori = 0

        try:
            conn = self._connect()
        except (imaplib.IMAP4.error, OSError) as exc:
            logger.error("IMAP %s: connessione fallita: %s", casella, exc)
            return FetchResult(
                casella=casella, scaricati=0, duplicati=0, errori=1, nuovi_id=[]
            )

        try:
            # UID-based search: tutti gli UID superiori a quello già visto.
            criterion = f"UID {last_uid + 1}:*" if last_uid else "ALL"
            typ, data = conn.uid("SEARCH", None, criterion)
            if typ != "OK":
                logger.error("IMAP %s: SEARCH fallita: %s %s", casella, typ, data)
                return FetchResult(
                    casella=casella, scaricati=0, duplicati=0, errori=1, nuovi_id=[]
                )
            uids = [int(x) for x in (data[0] or b"").split() if x.isdigit()]
            # Filtriamo extra-safe: alcuni server restituiscono `<last_uid>` quando
            # non ci sono messaggi più nuovi.
            uids = [u for u in uids if u > last_uid][:max_messages]
            logger.info("IMAP %s: %d nuovi UID da processare", casella, len(uids))

            for uid in uids:
                try:
                    typ, msgdata = conn.uid("FETCH", str(uid).encode(), "(RFC822)")
                    if typ != "OK" or not msgdata or not isinstance(msgdata[0], tuple):
                        logger.warning("IMAP %s: FETCH UID=%s fallita", casella, uid)
                        errori += 1
                        continue
                    raw_bytes: bytes = msgdata[0][1]  # type: ignore[index]
                    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
                    if not isinstance(msg, EmailMessage):
                        # Fallback: ricostruisci come EmailMessage
                        msg = EmailMessage()
                        msg.set_content("")

                    message_id = (msg.get("Message-ID") or "").strip()
                    in_reply_to = (msg.get("In-Reply-To") or "").strip()
                    references = (msg.get("References") or "").strip()
                    sender = (msg.get("From") or "").strip()
                    recipients = (msg.get("To") or "").strip()
                    subject = (msg.get("Subject") or "").strip()
                    received = _parse_received_date(msg)
                    body_text = _extract_body_text(msg)
                    has_att = _has_attachments(msg)

                    eml_path = _archive_eml(
                        Path(archivio_root),
                        casella=casella,
                        received_at=received,
                        message_id=message_id,
                        subject=subject,
                        raw=raw_bytes,
                    )

                    inserted = repo.insert_mail(
                        casella=casella,
                        uid_imap=uid,
                        message_id=message_id,
                        in_reply_to=in_reply_to,
                        references=references,
                        sender=sender,
                        recipients=recipients,
                        subject=subject,
                        body_text=body_text,
                        has_attachments=has_att,
                        raw_eml_path=eml_path,
                        ricevuto_at=received,
                    )
                    if inserted is None:
                        duplicati += 1
                    else:
                        scaricati += 1
                        if inserted.id is not None:
                            nuovi_id.append(inserted.id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "IMAP %s: errore processando UID=%s: %s", casella, uid, exc
                    )
                    errori += 1
        finally:
            try:
                conn.close()
            except imaplib.IMAP4.error:
                pass
            conn.logout()

        logger.info(
            "IMAP %s: done. scaricati=%d duplicati=%d errori=%d",
            casella, scaricati, duplicati, errori,
        )
        return FetchResult(
            casella=casella,
            scaricati=scaricati,
            duplicati=duplicati,
            errori=errori,
            nuovi_id=nuovi_id,
        )
