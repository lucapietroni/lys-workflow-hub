"""Orchestratore di invio per le bozze M4.

Riusa `integrations/pec_mailer.py` (build_message + send_message), con lo
stesso schema di `risarcimento_vandalismo/invio_pec.py`:

  1. costruisce il MIME (subject + body + allegati);
  2. archivia il `.eml` su filesystem PRIMA del tentativo SMTP (niente
     messaggi persi su crash di rete);
  3. esegue l'invio (o dry-run);
  4. aggiorna lo stato della Draft a SENT, registra il path del `.eml`,
     e logga nel registro `pec_inviate` se la repository e' fornita.

Punto di ingresso: `spedisci(draft, params, draft_repo, ...)`.

Idempotenza: se la Draft e' gia' SENT, ritorna `Esito(ok=True, ...)`
senza rinviare.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lys_workflow_hub.core.draft_repository import (
    CHANNEL_PEC,
    Draft,
    DraftRepository,
    STATUS_CANCELLED,
    STATUS_SENT,
)
from lys_workflow_hub.core.pec_log_repository import (
    ESITO_DRY_RUN,
    ESITO_KO,
    ESITO_OK,
    PecLogRepository,
)
from lys_workflow_hub.integrations.pec_mailer import (
    BuiltMessage,
    SendResult,
    build_message,
    send_message,
)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Parametri di invio
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParametriSpedizione:
    """Tutto cio' che serve per spedire una bozza M4."""

    # Mittente
    sender_email: str
    sender_display: str
    reply_to: str = ""

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""

    # Operative
    dry_run: bool = False
    archivio_root: Path = Path("/tmp/LYSApp/Risposte_inviate")
    compagnia_nome: str = ""
    compagnia_id: int | None = None


@dataclass(frozen=True)
class EsitoSpedizione:
    """Esito dell'orchestrazione completa."""

    ok: bool
    dry_run: bool
    draft: Draft  # stato aggiornato (SENT in caso ok)
    eml_path: str = ""
    message_id: str = ""
    error: str = ""


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
    s = s.strip("_") or "x"
    return s[:max_len]


def _archivia_eml(
    *,
    archivio_root: Path,
    numero_pratica: int | None,
    compagnia_nome: str,
    message_id: str,
    eml_bytes: bytes,
    now: datetime,
) -> Path:
    """Salva il `.eml` partizionato per anno; nome deterministico.

    Stessa convenzione di `risarcimento_vandalismo.invio_pec._archivia_eml`,
    ma sotto un sub-archivio "Risposte" cosi' i PEC inviate del workflow B
    non si mescolano con le risposte M4.
    """
    anno_dir = Path(archivio_root) / str(now.year)
    anno_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    pratica_str = str(int(numero_pratica)) if numero_pratica is not None else "nopratica"
    fname = f"{timestamp}_risposta_{pratica_str}_{_slug(compagnia_nome)}.eml"
    target = anno_dir / fname
    if target.exists():
        suffix = _slug(message_id, max_len=8)
        target = anno_dir / f"{target.stem}_{suffix}.eml"
    target.write_bytes(eml_bytes)
    return target


def _body_to_plain(body: str) -> str:
    """Riduce eventuale HTML inline a testo. Conservativo, no parser: la
    pipeline M4 produce gia' plain text, ma se l'editor del cruscotto
    inserisce qualche tag, lo strippiamo qui."""
    if not body:
        return ""
    # Sostituisci <br/> e </p> con newline prima di rimuovere i tag.
    s = re.sub(r"(?i)<br\s*/?>", "\n", body)
    s = re.sub(r"(?i)</p\s*>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    # Decoding entita' base.
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return s


# --------------------------------------------------------------------------- #
#  Orchestrazione
# --------------------------------------------------------------------------- #


def spedisci(
    draft: Draft,
    *,
    params: ParametriSpedizione,
    draft_repo: DraftRepository,
    pec_log_repo: PecLogRepository | None = None,
    now: datetime | None = None,
) -> EsitoSpedizione:
    """Costruisce, archivia, spedisce e marca la Draft come SENT.

    Validazioni preliminari:
      * draft.status non puo' essere CANCELLED (ValueError);
      * draft.status SENT -> idempotente, ritorna esito ok senza rinviare;
      * draft.to_address vuoto -> errore esplicito;
      * subject vuoto o body vuoto -> errore esplicito.
    """
    # Validazioni di stato.
    if draft.status == STATUS_SENT:
        # Idempotenza.
        return EsitoSpedizione(
            ok=True,
            dry_run=False,
            draft=draft,
            eml_path=draft.sent_eml_path,
            message_id="",
        )
    if draft.status == STATUS_CANCELLED:
        raise ValueError(f"Draft {draft.id} annullata, non inviabile")

    # Validazioni di contenuto.
    if not draft.to_address or "@" not in draft.to_address:
        return EsitoSpedizione(
            ok=False,
            dry_run=False,
            draft=draft,
            error="Destinatario PEC mancante o non valido",
        )
    if not draft.subject:
        return EsitoSpedizione(
            ok=False, dry_run=False, draft=draft, error="Oggetto vuoto",
        )
    if not draft.body_html:
        return EsitoSpedizione(
            ok=False, dry_run=False, draft=draft, error="Corpo vuoto",
        )

    when = now or datetime.now()
    body_plain = _body_to_plain(draft.body_html)

    # 1) Build MIME (solo gli allegati con included=True).
    included_paths = [Path(a.path) for a in draft.attachments_included]
    try:
        built: BuiltMessage = build_message(
            sender_email=params.sender_email,
            sender_display=params.sender_display,
            recipient_email=draft.to_address,
            subject=draft.subject,
            body_text=body_plain,
            attachments=included_paths,
            reply_to=params.reply_to,
        )
    except (ValueError, FileNotFoundError) as exc:
        logger.warning("Build MIME fallita per draft %s: %s", draft.id, exc)
        return EsitoSpedizione(
            ok=False, dry_run=False, draft=draft, error=f"Build MIME: {exc}",
        )

    # 2) Archiviazione filesystem (PRIMA dell'invio).
    try:
        eml_path = _archivia_eml(
            archivio_root=Path(params.archivio_root),
            numero_pratica=draft.pratica_numero,
            compagnia_nome=params.compagnia_nome,
            message_id=built.message_id,
            eml_bytes=built.eml_bytes,
            now=when,
        )
    except OSError as exc:
        logger.exception("Archiviazione .eml fallita per draft %s", draft.id)
        return EsitoSpedizione(
            ok=False,
            dry_run=False,
            draft=draft,
            message_id=built.message_id,
            error=f"Archiviazione .eml: {exc}",
        )

    # 3) Invio reale (o dry-run).
    result: SendResult = send_message(
        built,
        smtp_host=params.smtp_host,
        smtp_port=params.smtp_port,
        smtp_user=params.smtp_user,
        smtp_password=params.smtp_password,
        sender_email=params.sender_email,
        recipient_email=draft.to_address,
        dry_run=params.dry_run,
    )

    # 4) Log nel registro pec_inviate (se fornito).
    if pec_log_repo is not None and draft.pratica_numero is not None:
        esito_str = (
            ESITO_DRY_RUN if result.dry_run
            else (ESITO_OK if result.ok else ESITO_KO)
        )
        try:
            pec_log_repo.log(
                numero_pratica=draft.pratica_numero,
                compagnia_id=params.compagnia_id,
                compagnia_nome=params.compagnia_nome or "",
                destinatario_pec=draft.to_address,
                mittente_pec=params.sender_email,
                oggetto=draft.subject,
                body=body_plain,
                allegati=[Path(a.path).name for a in draft.attachments_included],
                path_eml=str(eml_path),
                message_id=built.message_id,
                esito=esito_str,
                errore=result.error or "",
                data_invio=when,
            )
        except Exception as exc:  # noqa: BLE001
            # Log fallito non blocca l'invio: la draft viene comunque
            # marcata SENT se l'SMTP ha risposto OK.
            logger.warning(
                "Log su pec_inviate fallito per draft %s: %s", draft.id, exc
            )

    if not result.ok:
        # Invio fallito: NON marchiamo SENT (la bozza resta editabile per
        # ritentativi). L'.eml rimane archiviato per audit.
        return EsitoSpedizione(
            ok=False,
            dry_run=result.dry_run,
            draft=draft,
            eml_path=str(eml_path),
            message_id=built.message_id,
            error=result.error,
        )

    # 5) Mark sent sulla Draft.
    updated = draft_repo.mark_sent(
        draft.id,
        sent_eml_path=str(eml_path),
        channel=CHANNEL_PEC,
    )

    return EsitoSpedizione(
        ok=True,
        dry_run=result.dry_run,
        draft=updated,
        eml_path=str(eml_path),
        message_id=built.message_id,
    )


__all__ = [
    "ParametriSpedizione",
    "EsitoSpedizione",
    "spedisci",
]
