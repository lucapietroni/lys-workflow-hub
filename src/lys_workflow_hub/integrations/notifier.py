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


def send_push(
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
#  FCM push (app Android Capacitor — utenti esterni)
# --------------------------------------------------------------------------- #


def send_fcm_push(
    *,
    project_id: str,
    credentials_path: str,
    token: str,
    title: str,
    message: str,
    click_path: str = "",
    timeout_seconds: int = 10,
) -> tuple[bool, str]:
    """Pubblica una notifica push via FCM HTTP v1 su un singolo device token.

    A differenza di ntfy (path pubblico senza autenticazione), FCM HTTP v1
    richiede un access token OAuth2 minted da una service account Firebase —
    `google-auth` gestisce minting/refresh/caching di quel token; l'invio
    resta un semplice `requests.post()`, come per ntfy. Restituisce
    (success, errore), non solleva mai.
    """
    try:
        import requests
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import service_account
    except ImportError as exc:
        return False, f"dipendenze FCM non installate: {exc}"

    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        creds.refresh(GoogleAuthRequest())
    except Exception as exc:  # noqa: BLE001
        return False, f"errore credenziali FCM: {exc}"

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    payload = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": message},
            "data": {"click_path": click_path} if click_path else {},
            # Senza priorità esplicita FCM consegna a priorità "normal": sotto
            # Doze/App Standby Android può ritardare la consegna di minuti in
            # modo imprevedibile invece di svegliare subito il device — "high"
            # richiede consegna immediata (osservato: notifica arrivata con
            # ~5 minuti di ritardo, in modo non riproducibile, senza errori).
            "android": {"priority": "high"},
        }
    }
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=timeout_seconds,
        )
        if not (200 <= resp.status_code < 300):
            return False, f"FCM HTTP {resp.status_code}: {resp.text[:200]}"
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"errore FCM: {exc}"


# --------------------------------------------------------------------------- #
#  Email riassuntiva via SMTP
# --------------------------------------------------------------------------- #


def send_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    sender: str,
    recipient: str,
    subject: str,
    body_text: str,
    smtp_tls: str = "",
    timeout_seconds: int = 30,
) -> tuple[bool, str]:
    """Invia un'email semplice via SMTP.

    `smtp_tls`:
      - "ssl"      -> SMTPS implicito (tipicamente porta 465).
      - "starttls" -> connessione cleartext + STARTTLS (tipicamente porta 587).
      - "none"     -> nessuna cifratura (sconsigliato).
      - ""         -> auto: 465 -> ssl, altrimenti starttls.
    """
    if not (smtp_host and smtp_user and smtp_password and sender and recipient):
        return False, "SMTP non configurato"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="lysauto.local")
    msg.set_content(body_text or "", subtype="plain", charset="utf-8")

    mode = (smtp_tls or "").strip().lower()
    if not mode:
        mode = "ssl" if int(smtp_port) == 465 else "starttls"

    context = ssl.create_default_context()
    try:
        if mode == "ssl":
            with smtplib.SMTP_SSL(
                host=smtp_host, port=smtp_port,
                context=context, timeout=timeout_seconds,
            ) as smtp:
                smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
        elif mode == "none":
            with smtplib.SMTP(
                host=smtp_host, port=smtp_port, timeout=timeout_seconds,
            ) as smtp:
                smtp.ehlo()
                smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
        else:  # starttls (default)
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
    smtp_tls: str = "",
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
            ok, err = send_push(
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

    # 2) Email riassuntiva — esclude ALTRO senza pratica (spam, ricevute di
    #    sistema, pubblicità, notifiche Legalmail): non utili per l'operatore
    #    e non riguardano pratiche della carrozzeria.
    da_mostrare = [
        (m, c) for m, c in nuove
        if not (c.categoria == "altro" and c.pratica_numero is None)
    ]
    email_sent = False
    if alert_email and smtp_host and da_mostrare:
        subject = f"[LYS Hub] {len(da_mostrare)} nuove risposte assicurazioni"
        body = _format_summary_body(da_mostrare)
        ok, err = send_email(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            sender=smtp_sender or smtp_user,
            recipient=alert_email,
            subject=subject,
            body_text=body,
            smtp_tls=smtp_tls,
        )
        email_sent = ok
        if not ok:
            errors.append(err)
    else:
        logger.info("alert_email o SMTP vuoti: skip email riassuntiva")

    return NotifyResult(push_sent=push_sent, email_sent=email_sent, errors=errors)


# --------------------------------------------------------------------------- #
#  Notifiche di collaborazione (v3.0 fase 5) — note/eventi su pratica
# --------------------------------------------------------------------------- #
#
# A differenza di `notify_batch` (batch a fine ciclo di polling), queste sono
# chiamate in tempo reale dalle route POST di note/eventi (routes.py,
# routes_portale.py), subito dopo il salvataggio. Non devono MAI far fallire
# la richiesta HTTP che le ha innescate: qualunque errore è loggato e
# inghiottito qui, non propagato al chiamante.


def notify_push_nuova_attivita(
    *,
    ntfy_server: str,
    ntfy_topic: str,
    titolo: str,
    messaggio: str,
    click_url: str = "",
    disabled: bool = False,
) -> None:
    """Push su un topic ntfy.sh per attività di collaborazione su una pratica.

    Generica per topic: usata sia per notificare l'admin (topic globale
    `NTFY_TOPIC` in `.env`, quando un esterno scrive nota/evento) sia per
    notificare un singolo esterno sul proprio topic personale (v3.0 fase 5,
    parte D — self-service in `/portale/impostazioni`), quando è l'admin ad
    aggiornare una pratica assegnata."""
    if disabled or not (ntfy_topic and ntfy_server):
        return
    try:
        ok, err = send_push(
            server=ntfy_server,
            topic=ntfy_topic,
            title=titolo,
            message=messaggio,
            tags=["speech_balloon"],
            click_url=click_url,
        )
        if not ok:
            logger.warning("Notifica push collaborazione fallita: %s", err)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notifica push collaborazione fallita: %s", exc)


def notify_fcm_nuova_attivita(
    *,
    fcm_project_id: str,
    fcm_credentials_path: str,
    fcm_token: str,
    titolo: str,
    messaggio: str,
    click_path: str = "",
    disabled: bool = False,
) -> None:
    """Push FCM per attività di collaborazione su una pratica — analoga a
    `notify_push_nuova_attivita` ma per utenti esterni con l'app Capacitor
    installata (device token FCM invece di topic ntfy)."""
    if disabled or not (fcm_project_id and fcm_credentials_path and fcm_token):
        return
    try:
        ok, err = send_fcm_push(
            project_id=fcm_project_id,
            credentials_path=fcm_credentials_path,
            token=fcm_token,
            title=titolo,
            message=messaggio,
            click_path=click_path,
        )
        if not ok:
            logger.warning("Notifica FCM fallita: %s", err)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notifica FCM fallita: %s", exc)


def notify_esterno_nuova_attivita(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    smtp_sender: str,
    recipient: str,
    subject: str,
    body_text: str,
    smtp_tls: str = "",
    disabled: bool = False,
) -> None:
    """Email al collaboratore esterno assegnato quando l'admin scrive una
    nota o aggiunge un evento sulla sua pratica."""
    if disabled or not recipient:
        return
    try:
        ok, err = send_email(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            sender=smtp_sender or smtp_user,
            recipient=recipient,
            subject=subject,
            body_text=body_text,
            smtp_tls=smtp_tls,
        )
        if not ok:
            logger.warning("Notifica email esterno (collaborazione) fallita: %s", err)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notifica email esterno (collaborazione) fallita: %s", exc)
