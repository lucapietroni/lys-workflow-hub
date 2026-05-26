"""Generatore di testi PEC per escalation SLA (M6.1).

Tre livelli di escalation per PEC inviate alle compagnie senza risposta:

  Livello 1 — Sollecito: cortese reminder, tono professionale.
  Livello 2 — Sollecito formale: più urgente, riferisce termine di 15 gg.
  Livello 3 — Diffida formale: ultimo avviso prima di procedere legalmente.

Ritorna (subject, body_html) pronti per il record Sollecito.
"""
from __future__ import annotations

import html
from datetime import datetime


LIVELLO_SOLLECITO = 1
LIVELLO_FORMALE   = 2
LIVELLO_DIFFIDA   = 3

LIVELLO_LABELS: dict[int, str] = {
    LIVELLO_SOLLECITO: "Sollecito",
    LIVELLO_FORMALE:   "Sollecito formale",
    LIVELLO_DIFFIDA:   "Diffida formale",
}

LIVELLO_BADGE_CLASS: dict[int, str] = {
    LIVELLO_SOLLECITO: "badge-yellow",
    LIVELLO_FORMALE:   "badge-orange",
    LIVELLO_DIFFIDA:   "badge-red",
}


def _h(text: str) -> str:
    return html.escape(text or "")


def genera_sollecito(
    *,
    livello: int,
    pratica_numero: int,
    compagnia_nome: str,
    data_invio: datetime,
    giorni_attesa: int,
    oggetto_originale: str,
    carrozzeria_nome: str = "Carrozzeria LYS Auto srl",
    carrozzeria_pec: str = "",
    carrozzeria_telefono: str = "",
    carrozzeria_referente: str = "",
) -> tuple[str, str]:
    """Genera (subject, body_html) per il livello di escalation richiesto.

    Tutti i parametri sono usati nel testo: ``data_invio`` è la data
    della PEC originale, ``giorni_attesa`` i giorni trascorsi senza risposta.
    """
    data_str = data_invio.strftime("%d/%m/%Y")

    sogg_prefix = {
        LIVELLO_SOLLECITO: "Sollecito —",
        LIVELLO_FORMALE:   "Sollecito formale —",
        LIVELLO_DIFFIDA:   "DIFFIDA FORMALE —",
    }.get(livello, "Sollecito —")

    subject = (
        f"{sogg_prefix} Pratica sinistro n. {pratica_numero} — "
        f"Richiesta risarcimento senza riscontro ({giorni_attesa} giorni)"
    )

    if livello == LIVELLO_SOLLECITO:
        intro = (
            f"Con la presente inviamo cortese sollecito in merito alla "
            f"nostra comunicazione del {_h(data_str)} "
            f"(oggetto: <em>{_h(oggetto_originale)}</em>), relativa alla "
            f"pratica sinistro n. <strong>{pratica_numero}</strong>, "
            f"ad oggi rimasta senza riscontro."
        )
        corpo = (
            "Siamo certi che si tratti di una mera dimenticanza e Vi "
            "preghiamo di voler fornire al più presto un aggiornamento "
            "in merito allo stato della pratica, al fine di evitare "
            "ulteriori solleciti."
        )
        chiusura = (
            "In attesa di un Vostro cortese riscontro, porgiamo "
            "distinti saluti."
        )
    elif livello == LIVELLO_FORMALE:
        intro = (
            f"Con la presente formalizziamo un ulteriore sollecito in "
            f"merito alla comunicazione trasmessa in data {_h(data_str)} "
            f"(oggetto: <em>{_h(oggetto_originale)}</em>), pratica sinistro "
            f"n. <strong>{pratica_numero}</strong>, "
            f"a tutt’oggi priva di risposta ({giorni_attesa} giorni)."
        )
        corpo = (
            "Vi richiamiamo alla necessità di un riscontro nei "
            "termini di legge. In assenza di comunicazione entro i "
            "prossimi 15 giorni dal ricevimento della presente, ci "
            "vedremo costretti ad adottare le misure opportune a tutela "
            "dei diritti del nostro assistito."
        )
        chiusura = "In attesa di urgente riscontro, porgiamo distinti saluti."
    else:  # LIVELLO_DIFFIDA
        intro = (
            f"Con la presente Vi diffiamo formalmente a provvedere in "
            f"merito alla richiesta di risarcimento trasmessa il "
            f"{_h(data_str)} (oggetto: <em>{_h(oggetto_originale)}</em>), "
            f"pratica sinistro n. <strong>{pratica_numero}</strong>, "
            f"ad oggi senza alcun riscontro ({giorni_attesa} giorni)."
        )
        corpo = (
            "Si invita ad ottemperare entro e non oltre 10 giorni dal "
            "ricevimento della presente, pena il ricorso alle azioni "
            "legali e/o amministrative previste dalla vigente normativa "
            "in materia di risarcimento danni "
            "(artt. 148 e ss. Codice delle Assicurazioni Private, "
            "D.Lgs. 209/2005)."
        )
        chiusura = (
            "<strong>La presente vale come atto di messa in mora.</strong>"
        )

    contatti: list[str] = []
    if carrozzeria_pec:
        contatti.append(f"PEC: {_h(carrozzeria_pec)}")
    if carrozzeria_telefono:
        contatti.append(f"Tel: {_h(carrozzeria_telefono)}")
    contatti_html = " — ".join(contatti)

    firma_parts: list[str] = [_h(carrozzeria_nome)]
    if carrozzeria_referente:
        firma_parts.append(_h(carrozzeria_referente))
    if contatti_html:
        firma_parts.append(contatti_html)
    firma_html = " — ".join(firma_parts)

    body_html = (
        f"<p>Gentili Signori,</p>\n"
        f"<p>{intro}</p>\n"
        f"<p>{corpo}</p>\n"
        f"<p>{chiusura}</p>\n"
        f"<hr>\n"
        f"<p><small>{firma_html}</small></p>"
    )

    return subject, body_html
