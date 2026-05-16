"""Scaffold (cornice fissa) delle risposte M4.

Strato esterno del modello a tre strati descritto nelle decisioni di
progetto:

  * intestazione "Spett.le ..."
  * riferimenti pratica / sinistro / polizza
  * apertura "Spett.le Compagnia,"
  * <CORPO LIBERO GENERATO DALL'AI>
  * chiusura "Distinti saluti / Carrozzeria LYS Auto srl / Referente"
  * data + citta

L'AI scrive SOLO il corpo: lo scaffold ci pensa a riferimenti e firma.
Cosi' la cornice e' identica per tutti i messaggi (audit-friendly,
coerente, e l'AI non puo' sbagliare la firma).

Plain text uguale al pec_generator del workflow Vandalismo, perche':
  * PEC e' tradizionalmente testo;
  * pec_mailer.build_message usa `set_content(body, subtype='plain')`.

Lo scaffold accetta un `ScaffoldContext` immutabile con i dati che
servono: la route web lo costruisce a partire da Pratica WinCar +
Compagnia + Settings; in test puoi crearlo a mano.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable


# --------------------------------------------------------------------------- #
#  Contesto
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScaffoldContext:
    """Dati necessari a costruire la cornice della risposta.

    Tutti i campi stringa accettano vuoto: lo scaffold omette la riga
    quando il valore manca.
    """

    # Destinatario (compagnia)
    compagnia_nome: str
    compagnia_indirizzo_compatto: str = ""
    compagnia_ufficio_sinistri: str = ""
    compagnia_pec: str = ""

    # Riferimenti pratica/sinistro/polizza
    pratica_numero: int | None = None
    sinistro_numero: str = ""
    polizza_numero: str = ""
    veicolo_targa: str = ""
    assicurato_nome: str = ""

    # Mittente (carrozzeria)
    carrozzeria_nome: str = "Carrozzeria LYS Auto srl"
    carrozzeria_referente: str = ""
    carrozzeria_pec: str = ""
    carrozzeria_email: str = ""
    carrozzeria_telefono: str = ""
    carrozzeria_comune: str = ""

    # Riferimento al thread originale (per Oggetto della risposta)
    subject_originale: str = ""


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _val(value: str | None) -> str:
    """Placeholder visibile per i campi vuoti."""
    if value is None:
        return ""
    return str(value).strip()


def _filter_empty(lines: Iterable[str]) -> list[str]:
    return [line for line in lines if line and line.strip()]


# --------------------------------------------------------------------------- #
#  Subject
# --------------------------------------------------------------------------- #


_RE_SUBJECT_PREFIX = ("re:", "r:", "fwd:", "fw:", "i:")


def _strip_reply_prefix(subj: str) -> str:
    """Rimuove eventuali 'Re: Re: Re:' multipli dall'oggetto originale."""
    s = (subj or "").strip()
    changed = True
    while changed:
        changed = False
        low = s.lower()
        for pref in _RE_SUBJECT_PREFIX:
            if low.startswith(pref):
                s = s[len(pref):].lstrip(" :")
                changed = True
                break
    return s


def build_subject(ctx: ScaffoldContext) -> str:
    """Compone l'Oggetto della risposta.

    Forma: ``Re: <oggetto originale ripulito> - Ns. rif. pratica <N>``.
    Se manca l'oggetto originale, fallback a un subject sintetico con i
    riferimenti.
    """
    base = _strip_reply_prefix(ctx.subject_originale)
    if base:
        out = f"Re: {base}"
    else:
        out = "Riscontro alla Vs. comunicazione"

    riferimenti: list[str] = []
    if ctx.pratica_numero is not None:
        riferimenti.append(f"Ns. rif. pratica {ctx.pratica_numero}")
    if ctx.sinistro_numero:
        riferimenti.append(f"Sinistro {ctx.sinistro_numero}")
    if ctx.veicolo_targa:
        riferimenti.append(f"Targa {ctx.veicolo_targa}")
    if riferimenti:
        out = f"{out} - {' - '.join(riferimenti)}"
    return out


# --------------------------------------------------------------------------- #
#  Corpo
# --------------------------------------------------------------------------- #


def _intestazione_destinatario(ctx: ScaffoldContext) -> list[str]:
    nome = _val(ctx.compagnia_nome) or "Spettabile Compagnia"
    out = [f"Spett.le {nome}"]
    if ctx.compagnia_ufficio_sinistri:
        out.append(f"c.a. {ctx.compagnia_ufficio_sinistri}")
    if ctx.compagnia_indirizzo_compatto:
        out.append(ctx.compagnia_indirizzo_compatto)
    if ctx.compagnia_pec:
        out.append(f"PEC: {ctx.compagnia_pec}")
    return out


def _sezione_riferimenti(ctx: ScaffoldContext) -> list[str]:
    out: list[str] = ["RIFERIMENTI"]
    if ctx.pratica_numero is not None:
        out.append(f"- Pratica nostra: {ctx.pratica_numero}")
    if ctx.sinistro_numero:
        out.append(f"- Numero sinistro: {ctx.sinistro_numero}")
    if ctx.polizza_numero:
        out.append(f"- Numero polizza: {ctx.polizza_numero}")
    if ctx.veicolo_targa:
        out.append(f"- Veicolo: targa {ctx.veicolo_targa}")
    if ctx.assicurato_nome:
        out.append(f"- Assicurato: {ctx.assicurato_nome}")
    if len(out) == 1:
        # Nessun riferimento: omettiamo del tutto la sezione.
        return []
    return out


def _firma(ctx: ScaffoldContext, *, oggi: date) -> list[str]:
    out = [
        "Distinti saluti.",
        "",
        f"{_val(ctx.carrozzeria_comune) or ''}, {oggi.strftime('%d/%m/%Y')}".lstrip(", "),
        "",
        f"Per {_val(ctx.carrozzeria_nome)}",
    ]
    if ctx.carrozzeria_referente:
        out.append(ctx.carrozzeria_referente)
    contatti: list[str] = []
    if ctx.carrozzeria_pec:
        contatti.append(f"PEC: {ctx.carrozzeria_pec}")
    if ctx.carrozzeria_email:
        contatti.append(f"Email: {ctx.carrozzeria_email}")
    if ctx.carrozzeria_telefono:
        contatti.append(f"Tel: {ctx.carrozzeria_telefono}")
    if contatti:
        out.append(" - ".join(contatti))
    return out


def build_body(
    body_ai: str,
    ctx: ScaffoldContext,
    *,
    oggi: date | None = None,
) -> str:
    """Costruisce il corpo completo: intestazione + riferimenti + corpo AI
    + firma. `body_ai` e' lo strato interno generato da Claude (solo
    contenuto, niente saluti e niente firma).

    Pulisce il body AI da eventuali saluti finali che il modello potrebbe
    aver infilato nonostante il prompt lo vieti (best-effort, vedi
    `_strip_chiusure_residue`).
    """
    oggi = oggi or date.today()

    body_clean = _strip_chiusure_residue(body_ai or "")

    blocchi: list[list[str]] = []
    blocchi.append(_intestazione_destinatario(ctx))

    rif = _sezione_riferimenti(ctx)
    if rif:
        blocchi.append(rif)

    blocchi.append([f"Oggetto: {build_subject(ctx)}"])
    blocchi.append(["Spett.le Compagnia,", "", body_clean.strip()])
    blocchi.append(_firma(ctx, oggi=oggi))

    return "\n\n".join("\n".join(_filter_empty(b)) for b in blocchi)


# --------------------------------------------------------------------------- #
#  Cleanup del body AI
# --------------------------------------------------------------------------- #


_CHIUSURE_DA_RIMUOVERE = (
    "distinti saluti",
    "cordiali saluti",
    "in attesa di un cortese riscontro",
    "in attesa di vostro cortese riscontro",
    "in attesa di vs. cortese riscontro",
    "in attesa di un vostro riscontro",
    "in attesa di gentile riscontro",
)


def _strip_chiusure_residue(text: str) -> str:
    """Rimuove dall'AI eventuali formule di chiusura messe nonostante il prompt.

    Strategia conservativa: rimuove SOLO se la chiusura compare nelle
    ultime due righe non vuote del body (cioe' alla fine). Non tocca
    occorrenze in mezzo al testo (che potrebbero essere intenzionali).
    """
    if not text:
        return ""
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""

    # Guarda le ultime 2 righe.
    tail_idx = max(0, len(lines) - 2)
    tail_join = " ".join(line.strip().lower() for line in lines[tail_idx:])
    for marker in _CHIUSURE_DA_RIMUOVERE:
        if marker in tail_join:
            lines = lines[:tail_idx]
            break
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Anonimizzazione minima del testo originale (per il prompt AI)
# --------------------------------------------------------------------------- #


def anonimizza_testo_originale(text: str, ctx: ScaffoldContext) -> str:
    """Sostituisce nel testo originale i dati personali noti con segnaposto.

    Conservativa: agisce solo sui valori che conosciamo dal contesto
    (assicurato, targa). Non fa parsing libero per evitare false positive
    su frasi della compagnia. Per la mitigazione GDPR completa servira'
    poi un modulo dedicato.
    """
    s = text or ""
    if ctx.assicurato_nome:
        s = s.replace(ctx.assicurato_nome, "[ASSICURATO]")
    if ctx.veicolo_targa:
        s = s.replace(ctx.veicolo_targa.upper(), "[TARGA]")
        s = s.replace(ctx.veicolo_targa.lower(), "[TARGA]")
    return s


__all__ = [
    "ScaffoldContext",
    "build_subject",
    "build_body",
    "anonimizza_testo_originale",
]
