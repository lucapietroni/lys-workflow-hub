"""Generatore del corpo (e oggetto) della PEC di richiesta risarcimento vandalismo.

Produce esclusivamente **testo** (subject + body). Il formato della PEC scelto
in fase di analisi è: messaggio email completo con corpo testuale lungo +
allegati (foto, denuncia, cessione del credito, eventuale documentazione
accessoria). Nessun PDF ausiliario viene generato in questa fase.

Uso tipico:

    from lys_workflow_hub.workflows.risarcimento_vandalismo import (
        data as vand_data,
        pec_generator,
        allegati as vand_allegati,
    )

    d = vand_data.from_pratica(pratica, compagnia=compagnia, ...)
    allegati = vand_allegati.scan(archivio_root, pratica.numero)
    subject = pec_generator.build_subject(d)
    body = pec_generator.build_body(d, allegati=allegati)
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from lys_workflow_hub.workflows.risarcimento_vandalismo.allegati import (
    Allegato,
    AllegatiPratica,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.data import (
    CARROZZERIA_CAP,
    CARROZZERIA_COMUNE,
    CARROZZERIA_NOME,
    CARROZZERIA_PIVA,
    CARROZZERIA_PROVINCIA,
    CARROZZERIA_VIA,
    RichiestaVandalismoData,
)


_PLACEHOLDER = "________"


def _val(value: str | None) -> str:
    """Valore visualizzato per i campi vuoti: placeholder ben visibile."""
    if value is None:
        return _PLACEHOLDER
    s = str(value).strip()
    return s if s else _PLACEHOLDER


def build_subject(data: RichiestaVandalismoData) -> str:
    """Oggetto della PEC. Sintetico ma con tutti i riferimenti utili."""
    targa = _val(data.veicolo_targa)
    polizza = _val(data.polizza_numero)
    return (
        f"Richiesta di risarcimento danni da atti vandalici - "
        f"Veicolo targato {targa} - Polizza n. {polizza}"
    )


def _intestazione_destinatario(data: RichiestaVandalismoData) -> list[str]:
    out: list[str] = []
    out.append(f"Spett.le {_val(data.polizza_compagnia_nome)}")
    if data.compagnia_ufficio_sinistri:
        out.append(f"c.a. {data.compagnia_ufficio_sinistri}")
    if data.compagnia_indirizzo_compatto:
        out.append(data.compagnia_indirizzo_compatto)
    if data.compagnia_pec:
        out.append(f"PEC: {data.compagnia_pec}")
    return out


def _sezione_assicurato(data: RichiestaVandalismoData) -> list[str]:
    lines = [
        "DATI ASSICURATO",
        f"- Nominativo: {_val(data.assicurato_nome_completo)}",
        f"- Codice fiscale: {_val(data.assicurato_codice_fiscale)}",
    ]
    if data.assicurato_data_nascita or data.assicurato_luogo_nascita:
        lines.append(
            f"- Nato/a a {_val(data.assicurato_luogo_nascita)} "
            f"il {_val(data.assicurato_data_nascita_formattata)}"
        )
    lines.append(f"- Residenza: {_val(data.residenza_compatta)}")
    if data.assicurato_telefono:
        lines.append(f"- Telefono: {data.assicurato_telefono}")
    if data.assicurato_email:
        lines.append(f"- Email: {data.assicurato_email}")
    if data.e_ditta:
        lines.append(
            f"- Legale rappresentante della ditta: {_val(data.ditta_nome)} "
            f"- P.IVA {_val(data.ditta_partita_iva)}"
        )
    return lines


def _sezione_polizza(data: RichiestaVandalismoData) -> list[str]:
    lines = [
        "DATI POLIZZA",
        f"- Compagnia: {_val(data.polizza_compagnia_nome)}",
        f"- Numero polizza: {_val(data.polizza_numero)}",
    ]
    if data.polizza_agenzia:
        lines.append(f"- Agenzia: {data.polizza_agenzia}")
    return lines


def _sezione_veicolo(data: RichiestaVandalismoData) -> list[str]:
    lines = [
        "DATI VEICOLO",
        f"- Marca e modello: {_val(data.veicolo_marca_modello)}",
        f"- Targa: {_val(data.veicolo_targa)}",
    ]
    if data.veicolo_telaio:
        lines.append(f"- Numero di telaio: {data.veicolo_telaio}")
    return lines


def _sezione_evento(data: RichiestaVandalismoData) -> list[str]:
    luogo_parti = [p for p in [data.evento_luogo_via, data.evento_luogo_comune] if p]
    luogo = ", ".join(luogo_parti) if luogo_parti else _PLACEHOLDER

    lines = [
        "DATI EVENTO",
        f"- Data dell'evento: {_val(data.evento_data_formattata)}",
    ]
    if data.evento_ora:
        lines.append(f"- Ora indicativa: {data.evento_ora}")
    lines.append(f"- Luogo: {luogo}")
    lines.append("- Descrizione dei danni accertati:")
    descrizione = (data.evento_descrizione_danni or "").strip() or _PLACEHOLDER
    for riga in descrizione.splitlines() or [descrizione]:
        lines.append(f"  {riga.strip()}")
    return lines


def _sezione_denuncia(data: RichiestaVandalismoData) -> list[str]:
    lines = [
        "DENUNCIA ALLE AUTORITÀ COMPETENTI",
        f"- Autorità: {_val(data.denuncia_autorita)}",
    ]
    if data.denuncia_comando:
        lines.append(f"- Comando / Ufficio: {data.denuncia_comando}")
    lines.append(f"- Data presentazione: {_val(data.denuncia_data_formattata)}")
    if data.denuncia_protocollo:
        lines.append(f"- Numero protocollo: {data.denuncia_protocollo}")
    lines.append("Copia della denuncia è allegata alla presente comunicazione.")
    return lines


def _sezione_cessione() -> list[str]:
    return [
        "CESSIONE DEL CREDITO",
        f"L'Assicurato ha sottoscritto cessione del credito a favore di "
        f"{CARROZZERIA_NOME} (P.IVA {CARROZZERIA_PIVA}), che provvederà "
        "all'esecuzione delle riparazioni e che resta unico soggetto legittimato",
        "a ricevere il pagamento del risarcimento per i danni materiali ed ogni "
        "ulteriore voce conseguente. Copia della cessione, debitamente firmata, "
        "è allegata alla presente.",
    ]


def _format_allegati(allegati: AllegatiPratica | None) -> list[str]:
    """Elenco numerato dei file allegati alla PEC."""
    out: list[str] = ["DOCUMENTI ALLEGATI"]
    if allegati is None or not allegati.tutti:
        out.append("- (vedere file allegati al presente messaggio)")
        return out

    n = 1
    if allegati.denunce:
        for f in allegati.denunce:
            out.append(f"{n}. Denuncia/Verbale - {f.nome_file}")
            n += 1
    if allegati.cessioni:
        for f in allegati.cessioni:
            out.append(f"{n}. Cessione del credito firmata - {f.nome_file}")
            n += 1
    if allegati.foto:
        out.append(
            f"{n}. Documentazione fotografica del danno "
            f"({len(allegati.foto)} immagini): "
            + ", ".join(f.nome_file for f in allegati.foto)
        )
        n += 1
    for f in allegati.altri:
        out.append(f"{n}. {f.nome_file}")
        n += 1
    return out


def _sezione_richiesta() -> list[str]:
    return [
        "RICHIESTA",
        "Tutto quanto sopra premesso, si richiede a codesta spettabile Compagnia "
        "di voler attivare con cortese sollecitudine la procedura di liquidazione "
        "dei danni in oggetto, ai sensi della garanzia accessoria \"Atti vandalici\" "
        "(o \"Eventi sociopolitici\") prevista dal contratto in essere, "
        "provvedendo a:",
        "  1) nominare il perito incaricato della valutazione del danno;",
        "  2) comunicare al cessionario sotto indicato i riferimenti del perito "
        "incaricato affinché possa essere concordato l'appuntamento per "
        "l'ispezione del veicolo;",
        "  3) liquidare il risarcimento direttamente alla Cessionaria, nei termini "
        "di legge e di polizza, al netto della franchigia eventualmente prevista.",
    ]


def _sezione_contatti(data: RichiestaVandalismoData) -> list[str]:
    indirizzo = f"{CARROZZERIA_VIA}, {CARROZZERIA_CAP} {CARROZZERIA_COMUNE} ({CARROZZERIA_PROVINCIA})"
    lines = [
        "RIFERIMENTI PER LE COMUNICAZIONI",
        f"{CARROZZERIA_NOME}",
        indirizzo,
        f"P.IVA {CARROZZERIA_PIVA}",
    ]
    if data.carrozzeria_pec:
        lines.append(f"PEC: {data.carrozzeria_pec}")
    if data.carrozzeria_email:
        lines.append(f"Email: {data.carrozzeria_email}")
    if data.carrozzeria_telefono:
        lines.append(f"Tel: {data.carrozzeria_telefono}")
    return lines


def build_body(
    data: RichiestaVandalismoData,
    *,
    allegati: AllegatiPratica | None = None,
    oggi: date | None = None,
) -> str:
    """Costruisce il corpo testuale completo della PEC.

    `allegati` viene usato per produrre l'elenco numerato. Se None, viene
    indicato genericamente "(vedere file allegati al presente messaggio)".
    """
    oggi = oggi or date.today()

    blocchi: list[list[str]] = []

    blocchi.append(_intestazione_destinatario(data))

    blocchi.append([
        "Oggetto: " + build_subject(data),
    ])

    apertura = (
        f"con la presente comunicazione, in nome e per conto del Sig./Sig.ra "
        f"{_val(data.assicurato_nome_completo)} (di seguito \"Assicurato\"), "
        f"contraente della polizza in oggetto, ai sensi della garanzia "
        f"accessoria \"Atti vandalici\" prevista dal contratto, si trasmette "
        f"formale richiesta di risarcimento per i danni subiti dal veicolo "
        f"meglio identificato in dispositivo, a seguito di atti vandalici "
        f"verificatisi in data {_val(data.evento_data_formattata)} e "
        f"regolarmente denunciati alle autorità competenti."
    )
    blocchi.append(["Spett.le Compagnia,", "", apertura])

    blocchi.append(_sezione_assicurato(data))
    blocchi.append(_sezione_polizza(data))
    blocchi.append(_sezione_veicolo(data))
    blocchi.append(_sezione_evento(data))
    blocchi.append(_sezione_denuncia(data))
    blocchi.append(_sezione_cessione())
    blocchi.append(_format_allegati(allegati))
    blocchi.append(_sezione_richiesta())
    blocchi.append(_sezione_contatti(data))

    chiusura = [
        "In attesa di cortese e sollecito riscontro, si porgono distinti saluti.",
        "",
        f"{CARROZZERIA_COMUNE}, {oggi.strftime('%d/%m/%Y')}",
        "",
        f"Per {CARROZZERIA_NOME}",
    ]
    blocchi.append(chiusura)

    # Unione con doppia newline tra blocchi e singola dentro ciascun blocco.
    return "\n\n".join("\n".join(b) for b in blocchi)


def filename_bozza(data: RichiestaVandalismoData, oggi: date | None = None) -> str:
    """Nome standard del file di bozza eventualmente scaricato dall'utente."""
    oggi = oggi or date.today()
    nome_safe = "".join(
        c if c.isalnum() else "_" for c in (data.assicurato_nome_completo or "")
    ).strip("_") or "assicurato"
    return (
        f"PEC_vandalismo_{data.numero_pratica}_{nome_safe}_"
        f"{oggi.strftime('%Y%m%d')}.txt"
    )


def build_all(
    data: RichiestaVandalismoData,
    *,
    allegati: AllegatiPratica | None = None,
    oggi: date | None = None,
) -> dict[str, str]:
    """Comodità per le route: subject + body + nome file bozza in un dict."""
    return {
        "subject": build_subject(data),
        "body": build_body(data, allegati=allegati, oggi=oggi),
        "filename": filename_bozza(data, oggi=oggi),
    }


def selezione_nomi_default(allegati: AllegatiPratica) -> Iterable[str]:
    """Elenco di nomi file pre-selezionati nella checklist allegati.

    Usato dalla schermata di anteprima: per default si selezionano la cessione
    firmata più recente, le denunce e tutte le foto. Gli "altri" no.
    """
    nomi: list[str] = []
    if allegati.cessioni:
        nomi.append(allegati.cessioni[0].nome_file)
    nomi.extend(a.nome_file for a in allegati.denunce)
    nomi.extend(a.nome_file for a in allegati.foto)
    return nomi


__all__ = [
    "Allegato",
    "build_all",
    "build_body",
    "build_subject",
    "filename_bozza",
    "selezione_nomi_default",
]
