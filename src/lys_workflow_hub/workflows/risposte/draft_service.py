"""Servizio di orchestrazione M4 — generazione e gestione delle bozze di
risposta alle compagnie.

Pipeline complessiva
--------------------

  M3 classifica la mail
        |
        v
  policy_per(categoria) ?
        +-> NESSUNA       -> stop
        +-> OPT_IN        -> stop (aspetta azione manuale)
        +-> AUTO          -> crea_bozza_se_serve(...)
                                  |
                                  v
                        (a) costruisci ScaffoldContext (route-side)
                        (b) genera_body() -> testo libero da Claude API
                        (c) build_subject + build_body dello scaffold
                        (d) suggerisci() allegati dalla cartella pratica
                        (e) salva Draft in stato PENDING
                                  |
                                  v
                        Editor del cruscotto
                        aggiorna_bozza() -> status READY
                                  |
                                  v
                        invia_bozza() -> sender.spedisci()
                        -> PEC/SMTP + archivio .eml + status SENT
                                  |
                                  v
                        M3 ricomincia a monitorare la casella per la
                        controrisposta della compagnia

Contratto importante
--------------------

Tutte le funzioni sono *idempotenti dove sensato* e *sincrone*. Le
dipendenze esterne (Claude API, scansione cartella, SMTP) sono passate
esplicitamente al chiamante per facilitare test e mock.

Se la configurazione esterna manca (es. `api_key=""` o `archivio_root=None`),
la pipeline degrada graziosamente:
  * AI assente -> body fallback con riassunto M3, costo zero.
  * Archivio pratica assente -> nessun allegato pre-spuntato.

In nessun caso `crea_bozza_se_serve` solleva eccezioni che bloccano il
polling (eccetto stato logicamente invalido in DB).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from lys_workflow_hub.core.draft_repository import (
    CHANNEL_PEC,
    Draft,
    DraftAttachment,
    DraftRepository,
    STATUS_CANCELLED,
    STATUS_READY,
    STATUS_SENT,
)
from lys_workflow_hub.core.mail_in_repository import (
    MailClassificata,
    MailIn,
    MailRepository,
)
from lys_workflow_hub.workflows.risposte.attachments import (
    SuggestionResult,
    suggerisci as suggerisci_allegati,
)
from lys_workflow_hub.workflows.risposte.body_generator import (
    BodyGenerationResult,
    genera_body,
)
from lys_workflow_hub.workflows.risposte.categorie_policy import (
    BOZZA_AUTO,
    BOZZA_NESSUNA,
    BOZZA_OPT_IN,
    deve_generare_auto,
    policy_per,
)
from lys_workflow_hub.workflows.risposte.scaffold import (
    ScaffoldContext,
    anonimizza_testo_originale,
    build_body as build_scaffold_body,
    build_subject as build_scaffold_subject,
)
from lys_workflow_hub.workflows.risposte.sender import (
    EsitoSpedizione,
    ParametriSpedizione,
    spedisci,
)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Tipi di supporto
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GenerationResult:
    """Risultato unico della pipeline di generazione bozza."""

    subject: str
    body: str
    suggested_attachments: tuple[DraftAttachment, ...]
    ai_model: str
    ai_cost_eur: float
    ai_fallback: bool
    ai_fallback_reason: str = ""


# Ri-esportiamo per chi importa dal draft_service.
SendResult = EsitoSpedizione


# --------------------------------------------------------------------------- #
#  Costruzione del ScaffoldContext fallback (senza WinCar / Compagnia)
# --------------------------------------------------------------------------- #


def _scaffold_minimo_da_classificazione(
    classif: MailClassificata, mail: MailIn
) -> ScaffoldContext:
    """ScaffoldContext minimale costruito a partire dai soli dati M3.

    Usato quando il chiamante non fornisce un context arricchito (es.
    nei test, o nelle prime versioni in cui mail_poller non ha ancora il
    collegamento a WinCar+Compagnie). I campi mancanti diventano stringa
    vuota e lo scaffold li omette.
    """
    key = classif.key_facts or {}
    sinistro = str(key.get("numero_sinistro") or "")
    polizza_targa = ""  # M3 non estrae polizza tipicamente
    return ScaffoldContext(
        compagnia_nome="",
        pratica_numero=classif.pratica_numero,
        sinistro_numero=sinistro,
        polizza_numero=polizza_targa,
        subject_originale=mail.subject or "",
    )


# --------------------------------------------------------------------------- #
#  Pipeline di generazione (modulo + helper riusabile dalle route)
# --------------------------------------------------------------------------- #


def genera_bozza(
    *,
    classificazione: MailClassificata,
    mail: MailIn,
    scaffold_ctx: ScaffoldContext | None = None,
    archivio_root: Path | None = None,
    api_key: str = "",
    model: str = "claude-haiku-4-5-20251001",
    ai_disabled: bool = False,
) -> GenerationResult:
    """Esegue la pipeline di generazione completa.

    Non scrive nulla nel DB: torna i pezzi (subject, body, allegati,
    metadati) che `crea_bozza_se_serve` poi persiste con `insert_draft`.
    Esposto separatamente perche' utile alle route che vogliono mostrare
    una *preview* della bozza senza salvarla.
    """
    ctx = scaffold_ctx or _scaffold_minimo_da_classificazione(classificazione, mail)

    # 1) Anonimizza il testo originale (solo segnaposto dei dati noti del
    # contesto: dell'anonimizzazione completa GDPR si occupera' un modulo
    # dedicato in seguito).
    testo_anon = anonimizza_testo_originale(mail.body_text or "", ctx)

    # 2) Chiama l'AI per il corpo libero (o fallback se disabled/errore).
    gen: BodyGenerationResult = genera_body(
        categoria=classificazione.categoria,
        summary_m3=classificazione.summary or "",
        key_facts=classificazione.key_facts or {},
        testo_originale_anon=testo_anon,
        pratica_numero=ctx.pratica_numero,
        sinistro_numero=ctx.sinistro_numero,
        polizza_numero=ctx.polizza_numero,
        veicolo_targa=ctx.veicolo_targa,
        api_key=api_key,
        model=model,
        disabled=ai_disabled,
    )

    # 3) Incolla nello scaffold (intestazione + riferimenti + body + firma).
    subject = build_scaffold_subject(ctx)
    body_completo = build_scaffold_body(gen.body, ctx, oggi=date.today())

    # 4) Suggerisci allegati dalla cartella pratica (se la conosciamo).
    suggested: tuple[DraftAttachment, ...] = ()
    if archivio_root is not None and classificazione.pratica_numero is not None:
        try:
            sugg: SuggestionResult = suggerisci_allegati(
                archivio_root=Path(archivio_root),
                numero_pratica=classificazione.pratica_numero,
                categoria_m3=classificazione.categoria,
            )
            suggested = tuple(sugg.allegati)
        except Exception as exc:  # noqa: BLE001
            # Scansione cartella non deve mai bloccare la creazione bozza.
            logger.warning(
                "Scansione allegati fallita per pratica %s: %s",
                classificazione.pratica_numero, exc,
            )

    return GenerationResult(
        subject=subject,
        body=body_completo,
        suggested_attachments=suggested,
        ai_model=gen.ai_model,
        ai_cost_eur=gen.ai_cost_eur,
        ai_fallback=gen.fallback,
        ai_fallback_reason=gen.fallback_reason,
    )


# --------------------------------------------------------------------------- #
#  Punto di ingresso da M3
# --------------------------------------------------------------------------- #


def crea_bozza_se_serve(
    classificazione: MailClassificata,
    *,
    draft_repo: DraftRepository,
    mail_repo: MailRepository,
    scaffold_ctx: ScaffoldContext | None = None,
    archivio_root: Path | None = None,
    api_key: str = "",
    model: str = "claude-haiku-4-5-20251001",
    ai_disabled: bool = False,
    to_address: str = "",
    forza: bool = False,
    policy_override: dict[str, str] | None = None,
) -> Draft | None:
    """Hook chiamato da M3 al termine della classificazione.

    Logica:
      1. idempotenza: se esiste gia' un draft per la classificazione, lo
         ritorna senza ricrearlo;
      2. se la policy della categoria e' NESSUNA e `forza=False`, ritorna
         None (nessuna bozza creata);
      3. se policy e' AUTO oppure `forza=True`, genera la bozza completa
         (scaffold + AI + allegati) e la salva PENDING.

    Parametri opzionali:
      scaffold_ctx:
        contesto pre-costruito da WinCar+Compagnie. Se assente, viene
        costruito un context minimale dai dati di M3.
      archivio_root:
        radice di `C:\\WinCar\\Archivi`. Se assente, niente scansione
        allegati.
      api_key / model / ai_disabled:
        parametri per il body_generator. Se vuoti, fallback testuale.
      to_address:
        destinatario PEC pre-popolato. Se vuoto, usa il mittente della
        mail originale.
      policy_override:
        dizionario categoria -> policy caricato da `CategoriaPolicyRepository`
        (M5.2). Se None, usa il dizionario statico in `categorie_policy.py`.
        Permette di cambiare la policy dalla UI senza riavviare l'app.

    Ritorna:
      la `Draft` creata (o pre-esistente), o None se non serve bozza.
    """
    if classificazione.id is None:
        raise ValueError("Classificazione senza id (non persistita)")

    # 1) Idempotenza.
    esistente = draft_repo.get_by_classification(int(classificazione.id))
    if esistente is not None:
        logger.debug(
            "Draft gia' presente per mail_class_id=%s (id=%s, status=%s)",
            classificazione.id, esistente.id, esistente.status,
        )
        return esistente

    # 2) Policy — usa override da DB (M5.2) se disponibile, altrimenti statica.
    if policy_override is not None:
        _policy = policy_override.get(classificazione.categoria, BOZZA_OPT_IN)
    else:
        _policy = policy_per(classificazione.categoria)

    if _policy == BOZZA_NESSUNA and not forza:
        logger.debug(
            "Categoria %s: policy=nessuna, salto creazione bozza",
            classificazione.categoria,
        )
        return None
    if _policy != BOZZA_AUTO and not forza:
        logger.debug(
            "Categoria %s: policy=%s, attesa azione operatore",
            classificazione.categoria, _policy,
        )
        return None

    # 3) Generazione completa.
    mail = mail_repo.get_mail(classificazione.mail_in_id)
    if mail is None:
        raise ValueError(
            f"Mail in arrivo {classificazione.mail_in_id} non trovata"
        )

    gen = genera_bozza(
        classificazione=classificazione,
        mail=mail,
        scaffold_ctx=scaffold_ctx,
        archivio_root=archivio_root,
        api_key=api_key,
        model=model,
        ai_disabled=ai_disabled,
    )

    # to_address: pre-popola dal parametro o dal sender della mail; in
    # entrambi i casi passa attraverso il normalizzatore (idempotente su
    # email gia' pulite, ma essenziale quando la sorgente e' un From: PEC
    # incapsulato del tipo '"Per conto di: vero@x.it" <posta-certificata@y.it>'.
    raw_to = to_address.strip() or (mail.sender or "")
    destinatario = _destinatario_da_mittente(raw_to)

    draft = draft_repo.insert_draft(
        mail_class_id=int(classificazione.id),
        pratica_numero=classificazione.pratica_numero,
        subject=gen.subject,
        body_html=gen.body,
        to_address=destinatario,
        attachments=gen.suggested_attachments,
        ai_model=gen.ai_model,
        ai_cost_eur=gen.ai_cost_eur,
        channel=CHANNEL_PEC,
    )
    logger.info(
        "Bozza M4 creata: draft_id=%s, pratica=%s, categoria=%s, "
        "ai_fallback=%s, allegati_suggeriti=%s",
        draft.id, draft.pratica_numero, classificazione.categoria,
        gen.ai_fallback, len(gen.suggested_attachments),
    )
    return draft


_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_RE_PER_CONTO_DI = re.compile(
    r"per\s+conto\s+di[:\s]+([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)
_GATEWAY_PEC_MARKERS = ("posta-certificata@", "postacert@")


def _is_gateway_pec(email: str) -> bool:
    """True se l'email e' un gateway PEC tecnico (Aruba/InfoCert) e non
    un vero destinatario raggiungibile."""
    low = (email or "").lower()
    return any(marker in low for marker in _GATEWAY_PEC_MARKERS)


def _destinatario_da_mittente(sender_raw: str) -> str:
    """Estrae il vero indirizzo PEC del mittente da una stringa header `From`.

    Le PEC arrivano incapsulate dal provider: l'header `From` ha tipicamente
    forma `'"Per conto di: vero@dominio.it" <posta-certificata@gateway.it>'`
    dove `posta-certificata@` e' un mittente tecnico di sistema a cui *non*
    si puo' rispondere — il vero destinatario per il nostro reply e' quello
    nel `Per conto di:`.

    Strategia in ordine di preferenza:
      1. estrae l'email dopo "Per conto di:";
      2. estrae l'email tra `<...>` se non e' un gateway tecnico;
      3. cerca qualsiasi email nel testo che non sia un gateway tecnico;
      4. fallback: ritorna il sender raw cosi' com'e' (l'utente correggera').
    """
    if not sender_raw:
        return ""
    s = sender_raw.strip()

    # 1) Per conto di: vero@indirizzo.it
    m = _RE_PER_CONTO_DI.search(s)
    if m:
        return m.group(1).lower()

    # 2) Email tra angolari, escludendo gateway PEC tecnici.
    angolari = re.findall(r"<([^<>\s]+@[^<>\s]+)>", s)
    for em in angolari:
        if not _is_gateway_pec(em):
            return em.lower()

    # 3) Qualsiasi email nel testo, escludendo gateway PEC.
    for em in _RE_EMAIL.findall(s):
        if not _is_gateway_pec(em):
            return em.lower()

    # 4) Fallback: prima email trovata (anche se gateway) oppure stringa raw.
    if angolari:
        return angolari[0].lower()
    return s


# --------------------------------------------------------------------------- #
#  Editor / cruscotto
# --------------------------------------------------------------------------- #


def aggiorna_bozza(
    draft_id: int,
    *,
    draft_repo: DraftRepository,
    body_html: str | None = None,
    subject: str | None = None,
    to_address: str | None = None,
    cc_addresses: Iterable[str] | None = None,
    attachments: Iterable[DraftAttachment] | None = None,
    channel: str | None = None,
    mark_ready: bool = False,
) -> Draft:
    """Applica modifiche dall'editor del cruscotto.

    `mark_ready=True` porta lo stato da PENDING a READY.
    """
    status = STATUS_READY if mark_ready else None
    return draft_repo.update_draft(
        draft_id,
        subject=subject,
        body_html=body_html,
        to_address=to_address,
        cc_addresses=tuple(cc_addresses) if cc_addresses is not None else None,
        attachments=tuple(attachments) if attachments is not None else None,
        channel=channel,
        status=status,
    )


def annulla_bozza(
    draft_id: int,
    *,
    draft_repo: DraftRepository,
    reason: str = "",
) -> Draft:
    """Marca la bozza come annullata. Idempotente."""
    return draft_repo.mark_cancelled(draft_id, reason=reason)


# --------------------------------------------------------------------------- #
#  Invio
# --------------------------------------------------------------------------- #


def invia_bozza(
    draft_id: int,
    *,
    draft_repo: DraftRepository,
    params: ParametriSpedizione | None = None,
    pec_log_repo=None,
) -> EsitoSpedizione:
    """Invia la bozza via PEC e marca SENT.

    Se `params` e' None, ritorna idempotenza/errori senza tentare l'invio.
    Caso d'uso tipico: senza params, serve a verificare lo stato (es. se
    la bozza e' SENT ritorna `ok=True`; se e' CANCELLED solleva).

    Per inviare davvero, il chiamante (route web o mail_poller) costruisce
    `ParametriSpedizione` con `Settings` + dati compagnia e passa qui.
    """
    current = draft_repo.get_draft(draft_id)
    if current is None:
        raise ValueError(f"Draft {draft_id} inesistente")
    if current.status == STATUS_SENT:
        return EsitoSpedizione(
            ok=True,
            dry_run=False,
            draft=current,
            eml_path=current.sent_eml_path,
        )
    if current.status == STATUS_CANCELLED:
        raise ValueError(f"Draft {draft_id} annullata, non inviabile")
    if params is None:
        raise ValueError(
            "ParametriSpedizione mancanti: serve passare config SMTP "
            "e archivio_root per inviare davvero"
        )
    return spedisci(
        current,
        params=params,
        draft_repo=draft_repo,
        pec_log_repo=pec_log_repo,
    )


__all__ = [
    "GenerationResult",
    "SendResult",
    "ParametriSpedizione",
    "EsitoSpedizione",
    "genera_bozza",
    "crea_bozza_se_serve",
    "aggiorna_bozza",
    "annulla_bozza",
    "invia_bozza",
]
