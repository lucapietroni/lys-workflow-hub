"""Matching delle risposte assicurazione alle PEC inviate (M3).

Strategia ibrida:

1. **Header standard RFC 2822**: `In-Reply-To` o `References` contengono il
   Message-ID di una PEC che abbiamo inviato. Match diretto con
   `pec_inviate.message_id` → restituisce pratica + pec_inviata_id con
   `match_confidence=1.0`. È il caso "normale": la maggior parte dei
   gestionali sinistri risponde rispettando il thread.

2. **Euristica su oggetto + body**: alcune compagnie aprono nuovi thread
   senza In-Reply-To, oppure il gestionale sinistri rigenera l'oggetto.
   In quel caso cerchiamo nel testo:
     - Numero polizza (es. "Polizza n. 12345/678" o "polizza POL-ABC-2026")
     - Targa veicolo (es. "AB123CD")
     - Numero pratica WinCar (es. "Pratica 789", "rif. nostro sinistro 789")
   Se troviamo uno o più di questi, cerchiamo nelle PEC inviate l'ultima
   che matcha quei valori. Confidence proporzionale al numero di "segnali"
   convergenti.

3. **Nessun match**: la mail viene comunque archiviata e classificata, ma
   senza pratica/pec_inviata_id collegati. L'operatore può poi aggiungere
   il match manualmente (futura feature).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from lys_workflow_hub.core.mail_in_repository import MailIn
from lys_workflow_hub.core.pec_log_repository import PecInviata, PecLogRepository


logger = logging.getLogger(__name__)


# Metodi di match (matchano i valori salvati in mail_classificate.match_method)
METHOD_HEADER_IN_REPLY_TO = "header_in_reply_to"
METHOD_HEADER_REFERENCES = "header_references"
METHOD_HEURISTIC = "heuristic"
METHOD_NONE = "none"


@dataclass(frozen=True)
class MatchResult:
    pec_inviata_id: int | None
    pratica_numero: int | None
    method: str
    confidence: float  # 0..1
    pec_inviata: PecInviata | None = None  # comodo per la UI

    @property
    def matched(self) -> bool:
        return self.pec_inviata_id is not None


# --------------------------------------------------------------------------- #
#  Regex euristica
# --------------------------------------------------------------------------- #


# Targa italiana: 2 lettere + 3 cifre + 2 lettere (es. AB123CD).
# La targa è un identificatore molto forte: poche collisioni.
_RE_TARGA = re.compile(r"\b([A-Z]{2})\s*([0-9]{3})\s*([A-Z]{2})\b", re.IGNORECASE)

# Numero pratica WinCar: "Pratica 789", "pratica n. 789", "ns. rif. 789",
# evitando di matchare anni a 4 cifre o numeri troppo grossi/piccoli.
_RE_PRATICA = re.compile(
    r"\b(?:prat(?:ica)?|ns(?:\.|)\s*rif|nostro\s*rif|sinistro)\s*(?:n[°ºo.]?\s*)?"
    r"(\d{1,8})\b",
    re.IGNORECASE,
)

# Numero polizza: "Polizza n. xxxxx" oppure "polizza xxxxx" (max 30 caratteri,
# può contenere lettere/cifre/trattini/slash).
_RE_POLIZZA = re.compile(
    r"\bpolizza\s*(?:n[°ºo.]?\s*)?([A-Z0-9][A-Z0-9./\-]{2,29})\b",
    re.IGNORECASE,
)


def _normalizza_targa(raw_match: re.Match[str]) -> str:
    return f"{raw_match.group(1).upper()}{raw_match.group(2)}{raw_match.group(3).upper()}"


def _estrai_segnali(text: str) -> dict:
    """Estrae tutti i segnali utili al matching da una stringa (oggetto+body)."""
    text = text or ""
    targhe = {_normalizza_targa(m) for m in _RE_TARGA.finditer(text)}
    pratiche: set[int] = set()
    for m in _RE_PRATICA.finditer(text):
        try:
            n = int(m.group(1))
            # Filtriamo valori improbabili come pratiche (es. anni recenti).
            if 1 <= n <= 9_999_999:
                pratiche.add(n)
        except ValueError:
            continue
    polizze = {m.group(1).strip().upper() for m in _RE_POLIZZA.finditer(text)}
    return {"targhe": targhe, "pratiche": pratiche, "polizze": polizze}


# --------------------------------------------------------------------------- #
#  Match per header
# --------------------------------------------------------------------------- #


def _extract_message_ids(raw: str) -> list[str]:
    """Estrae i Message-ID `<...>` da una stringa (In-Reply-To o References)."""
    if not raw:
        return []
    return re.findall(r"<[^<>\s]+>", raw)


def _match_per_header(
    mail: MailIn, pec_repo: PecLogRepository
) -> MatchResult | None:
    """Tentativo di match via In-Reply-To / References."""
    # Carichiamo tutte le PEC inviate degli ultimi N giorni potrebbe essere
    # un'ottimizzazione, ma con qualche centinaio di righe è semplice prendere
    # tutto e cercare in memoria.
    candidates = pec_repo.list_all(limit=10_000)
    if not candidates:
        return None
    by_msgid = {p.message_id: p for p in candidates if p.message_id}

    # 1) In-Reply-To: ha priorità (è il padre diretto del thread).
    for msgid in _extract_message_ids(mail.in_reply_to):
        if msgid in by_msgid:
            p = by_msgid[msgid]
            return MatchResult(
                pec_inviata_id=p.id,
                pratica_numero=p.numero_pratica,
                method=METHOD_HEADER_IN_REPLY_TO,
                confidence=1.0,
                pec_inviata=p,
            )

    # 2) References: catena di thread (l'ultima è solitamente il messaggio
    # immediatamente precedente, prima il root).
    for msgid in _extract_message_ids(mail.references):
        if msgid in by_msgid:
            p = by_msgid[msgid]
            return MatchResult(
                pec_inviata_id=p.id,
                pratica_numero=p.numero_pratica,
                method=METHOD_HEADER_REFERENCES,
                confidence=0.95,
                pec_inviata=p,
            )

    return None


# --------------------------------------------------------------------------- #
#  Match euristico
# --------------------------------------------------------------------------- #


def _match_per_euristica(
    mail: MailIn, pec_repo: PecLogRepository
) -> MatchResult | None:
    """Tentativo di match cercando targa / numero pratica / numero polizza
    nell'oggetto + body della mail in arrivo, confrontandoli con i metadati
    delle PEC inviate.
    """
    segnali = _estrai_segnali(f"{mail.subject}\n{mail.body_text}")
    if not (segnali["targhe"] or segnali["pratiche"] or segnali["polizze"]):
        return None

    candidates = pec_repo.list_all(limit=10_000)
    if not candidates:
        return None

    # Per ogni candidato, contiamo quanti segnali convergono. Per le PEC
    # inviate i segnali "forti" sono:
    #   - numero_pratica (match diretto se compare nel testo)
    #   - targa  → cerchiamo dentro oggetto (e di solito è in oggetto)
    #   - polizza → cerchiamo dentro oggetto
    miglior_match: MatchResult | None = None
    miglior_score = 0.0

    for p in candidates:
        score = 0.0
        segnali_pratica = _estrai_segnali(p.oggetto)
        # match per pratica
        if p.numero_pratica in segnali["pratiche"]:
            score += 0.6
        # match per targa: se le targhe estratte dall'oggetto della PEC sono nei segnali della mail
        if segnali_pratica["targhe"] & segnali["targhe"]:
            score += 0.5
        # match per polizza: la PEC inviata ha la polizza dentro l'oggetto
        if segnali_pratica["polizze"] & segnali["polizze"]:
            score += 0.4

        # Bonus se la mail viene proprio dal destinatario di quella PEC
        # (es. risposta dalla stessa compagnia).
        if (
            p.destinatario_pec
            and p.destinatario_pec.lower() in (mail.sender or "").lower()
        ):
            score += 0.3

        if score > miglior_score:
            miglior_score = score
            miglior_match = MatchResult(
                pec_inviata_id=p.id,
                pratica_numero=p.numero_pratica,
                method=METHOD_HEURISTIC,
                # Cap a 0.9 — l'euristica non è mai sicura al 100%.
                confidence=min(round(score, 2), 0.9),
                pec_inviata=p,
            )

    # Soglia di accettazione: serve almeno 0.6 (un segnale forte).
    if miglior_match and miglior_score >= 0.6:
        return miglior_match
    return None


# --------------------------------------------------------------------------- #
#  API pubblica
# --------------------------------------------------------------------------- #


def match_mail(mail: MailIn, pec_repo: PecLogRepository) -> MatchResult:
    """Tenta il matching: prima header, poi euristica. Restituisce sempre un
    `MatchResult`; se nessun metodo trova nulla, `pec_inviata_id=None` e
    `method=METHOD_NONE`."""
    # 1) Header (alto vincolo, alta confidenza).
    header = _match_per_header(mail, pec_repo)
    if header is not None:
        return header

    # 2) Euristica (più rumoroso).
    eur = _match_per_euristica(mail, pec_repo)
    if eur is not None:
        return eur

    return MatchResult(
        pec_inviata_id=None,
        pratica_numero=None,
        method=METHOD_NONE,
        confidence=0.0,
    )
