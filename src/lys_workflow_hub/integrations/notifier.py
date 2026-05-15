"""Notifiche push (ntfy.sh) + email riassuntiva (M3).

Due canali, attivabili indipendentemente via `.env`:

  - **Push istantaneo (ntfy.sh)**: una notifica per ogni mail classificata
    con `action_required=True`. Suono+vibrazione sul telefono.
    Richiede `NTFY_TOPIC` (segreto, univoco) e app ntfy installata sul phone.

  - **Email riassuntiva**: a fine ciclo polling, una email con il riepilogo
    di tutte le nuove risposte classificate. Inviata via SMTP_HOST/USER/PASSWORD
    (mail.tophost.it ordinaria, non PEC). Vuota = niente email.

Se `notify_disabled=True` in .env, entrambi i canali vengono skippati
(modalità silenziosa). Usata anche nei test per non chiamare servizi esterni.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Iterable

from lys_workflow_hub.core.mail_in_repository import (
    CATEGORIA_LABELS,
    MailIn,
    MailClassificata,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotifyResult:
    push_sent: int
    email_sent: bool
    errors: list[str]


# --------------------------------------------------------------------------- #
#  ntfy.sh push
# --------------------------------------------------------------------------- #


def _send_push(
    *,
    server: str,
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    tags: Iterable[str] = (),
    click_url: str = "",
    timeout_seconds: int = 10,
) -> tuple[bool, str]:
    """Pubblica una notifica su ntfy.sh. Restituisce (success, errore)."""
    try:
        import requests
    except ImportError:
        return False, "requests non installato"

    url = f"{server.rstrip('/')}/{topic}"
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if click_url:
        headers["Click"] = click_url

    try:
        resp = requests.post(
            url,
            data=message.encode("utf-8"),
            headers={k: (v.decode("utf-8") if isinstance(v, bytes) else v) for k, v in headers.items()},
            timeout=timeout_seconds,
        )
        if not (200 <= resp.status_code < 300):
            return False, f"ntfy HTTP {resp.status_code}: {resp.text[:200]}"
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"errore ntfy: {exc}"


# --------------------------------------------------------------------------- #
#  Email riassuntiva via SMTP
# --------------------------------------------------------------------------- #


def _send_summary_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    sender: str,
    recipient: str,
    subject: str,
    body_text: str,
    timeout_seconds: int = 30,
) -> tuple[bool, str]:
    """Invia un'email semplice via SMTP (STARTTLS su 587 o SSL su 465)."""
    if not (smtp_host and smtp_user and smtp_password and sender and recipient):
        return False, "SMTP non configurato"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="lysauto.local")
    msg.set_content(body_text or "", subtype="plain", charset="utf-8")

    context = ssl.create_default_context()
    try:
        if int(smtp_port) == 465:
            with smtplib.SMTP_SSL(
                host=smtp_host, port=smtp_port,
                context=context, timeout=timeout_seconds,
            ) as smtp:
                smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(
                host=smtp_host, port=smtp_port, timeout=timeout_seconds,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"errore SMTP: {exc}"


# --------------------------------------------------------------------------- #
#  Composizione testi
# --------------------------------------------------------------------------- #


def _format_push(mail: MailIn, classif: MailClassificata) -> tuple[str, str]:
    """Title + body di una notifica push per una singola classificazione."""
    titolo = classif.categoria_label
    if classif.pratica_numero:
        titolo += f" · Pratica {classif.pratica_numero}"

    parts: list[str] = []
    if classif.summary:
        parts.append(classif.summary)
    parts.append(f"Da: {mail.sender}")
    if classif.key_facts:
        kf = classif.key_facts
        if kf.get("perito"):
            parts.append(f"Perito: {kf['perito']}")
        if kf.get("importo_eur") is not None:
            parts.append(f"Importo: € {kf['importo_eur']}")
        if kf.get("scadenza"):
            parts.append(f"Scadenza: {kf['scadenza']}")
        if kf.get("numero_sinistro"):
            parts.append(f"N. sinistro: {kf['numero_sinistro']}")

    return titolo, "\n".join(parts)


def _format_summary_body(
    nuove: list[tuple[MailIn, MailClassificata]]
) -> str:
    """Corpo testuale dell'email riassuntiva."""
    if not nuove:
        return "Nessuna nuova risposta in questa esecuzione."

    lines: list[str] = [
        f"Riepilogo nuove risposte ricevute ({len(nuove)}):",
        "",
    ]
    # Raggruppo per categoria.
    per_cat: dict[str, list[tuple[MailIn, MailClassificata]]] = {}
    for mail, c in nuove:
        per_cat.setdefault(c.categoria, []).append((mail, c))

    # Ordine fisso delle categorie (action_required first)
    ordine = [
        "nomina_perito",
        "richiesta_documenti",
        "liquidazione",
        "presa_in_carico",
        "altro",
    ]
    for cat in ordine:
        items = per_cat.get(cat)
        if not items:
            continue
        lines.append(f"== {CATEGORIA_LABELS.get(cat, cat).upper()} ({len(items)}) ==")
        for mail, c in items:
            prat = f"Pratica {c.pratica_numero}" if c.pratica_numero else "(no match)"
            lines.append(
                f"- [{prat}] {c.summary or '(no summary)'}"
            )
            lines.append(f"    Da: {mail.sender}")
            lines.append(f"    Oggetto: {mail.subject}")
        lines.append("")

    lines.append("---")
    lines.append("Notifica generata automaticamente da LYS Workflow Hub.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  API pubblica
# --------------------------------------------------------------------------- #


def notify_batch(
    *,
    nuove: list[tuple[MailIn, MailClassificata]],
    ntfy_server: str,
    ntfy_topic: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    smtp_sender: str,
    alert_email: str,
    base_url: str = "",
    disabled: bool = False,
) -> NotifyResult:
    """Manda push istantaneo per ogni mail action_required + email riassuntiva.

    - `nuove`: lista di tuple (MailIn, MailClassificata) appena classificate
      in questo ciclo di polling.
    - `base_url`: URL pubblico dell'app (es. http://192.168.1.42:8000) usato
      per costruire link "Click" nelle notifiche push.
    """
    if disabled or not nuove:
        return NotifyResult(push_sent=0, email_sent=False, errors=[])

    push_sent = 0
    errors: list[str] = []

    # 1) Push per ogni action_required
    if ntfy_topic and ntfy_server:
        for mail, classif in nuove:
            if not classif.action_required:
                continue
            title, body = _format_push(mail, classif)
            click = (
                f"{base_url.rstrip('/')}/risposte/{mail.id}"
                if base_url and mail.id else ""
            )
            ok, err = _send_push(
                server=ntfy_server,
                topic=ntfy_topic,
                title=title,
                message=body,
                priority="high" if classif.action_required else "default",
                tags=["warning"] if classif.action_required else ["mailbox"],
                click_url=click,
            )
            if ok:
                push_sent += 1
            else:
                errors.append(err)
    else:
        logger.info("ntfy_topic vuoto: skip notifiche push")

    # 2) Email riassuntiva
    email_sent = False
    if alert_email and smtp_host:
        subject = f"[LYS Hub] {len(nuove)} nuove risposte assicurazioni"
        body = _format_summary_body(nuove)
        ok, err = _send_summary_email(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            sender=smtp_sender or smtp_user,
            recipient=alert_email,
            subject=subject,
            body_text=body,
        )
        email_sent = ok
        if not ok:
            errors.append(err)
    else:
        logger.info("alert_email o SMTP vuoti: skip email riassuntiva")

    return NotifyResult(push_sent=push_sent, email_sent=email_sent, errors=errors)
