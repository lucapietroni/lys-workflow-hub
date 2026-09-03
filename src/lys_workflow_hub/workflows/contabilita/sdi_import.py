"""Import fatture (attive da WinCar, passive da SDI) → righe fattura + movimenti.

Contabilità gestionale, Fase 3.

Flusso attive:
  WinCar genera l'XML FatturaPA in ``sdi_wincar_attive_dir`` →
  :func:`importa_attive_da_dir` crea la riga ``contabilita_fattura``
  (stato ``da_inviare``) → :func:`invia_attive_pendenti` la trasmette allo SDI
  via :class:`~lys_workflow_hub.integrations.sdi.SdiClient` e crea un
  **movimento proposto in entrata**.

Flusso passive:
  :func:`sincronizza_passive` chiede al provider le fatture ricevute, crea la
  riga ``contabilita_fattura`` (tipo ``passiva``) + un **movimento proposto in
  uscita** senza categoria né pratica. Restano nella coda
  ``fattura_repo.list_non_collegate("passiva")`` (UI di smistamento: Fase 4).

Il parser XML è volutamente minimale: legge numero, data, controparte, importi
complessivi (nessun dettaglio per aliquota — l'IVA è informativa). Gestisce un
solo ``FatturaElettronicaBody`` per file (caso normale della carrozzeria).
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lys_workflow_hub.core.contabilita_fattura_repository import (
    ORIGINE_SDI,
    TIPO_ATTIVA,
    TIPO_PASSIVA,
    ContabilitaFatturaRepository,
    Fattura,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ORIGINE_FATTURA_SDI,
    STATO_CONFERMATO,
    STATO_PROPOSTO,
    TIPO_ENTRATA,
    TIPO_USCITA,
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.integrations.sdi import (
    FatturaAttivaPayload,
    SdiClient,
)

logger = logging.getLogger(__name__)

STATO_SDI_DA_INVIARE = "da_inviare"
STATO_SDI_INVIATA = "inviata"
STATO_SDI_SCARTATA = "scartata"
STATO_SDI_RICEVUTA = "ricevuta"
# Fattura attiva già trasmessa allo SDI da un altro canale (WinCar /
# commercialista): importata solo per la contabilità, mai re-inoltrata.
STATO_SDI_STORICO = "storico"

# TipoDocumento FatturaPA che sono note di credito (segno invertito nel movimento).
_TIPI_NOTA_CREDITO = {"TD04", "TD08"}


# --------------------------------------------------------------------------- #
#  Parser XML FatturaPA (minimale, namespace-agnostic)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FatturaXML:
    numero: str
    data: date
    tipo_documento: str
    cedente_piva: str
    cedente_nome: str
    cessionario_piva: str
    cessionario_nome: str
    imponibile: float
    imposta: float
    totale: float

    @property
    def anno(self) -> int:
        return self.data.year

    @property
    def is_nota_credito(self) -> bool:
        return self.tipo_documento.upper() in _TIPI_NOTA_CREDITO


def _lname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    if el is None:
        return None
    for c in list(el):
        if _lname(c.tag) == name:
            return c
    return None


def _path(el: ET.Element | None, *names: str) -> ET.Element | None:
    for n in names:
        el = _child(el, n)
    return el


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None and el.text else ""


def _num(s: str) -> float:
    if not s:
        return 0.0
    try:
        return round(float(s.replace(",", ".")), 2)
    except ValueError:
        return 0.0


def _anagrafica_nome(dati_anagrafici: ET.Element | None) -> str:
    an = _child(dati_anagrafici, "Anagrafica")
    denom = _text(_child(an, "Denominazione"))
    if denom:
        return denom
    nome = _text(_child(an, "Nome"))
    cognome = _text(_child(an, "Cognome"))
    return " ".join(p for p in (nome, cognome) if p)


def _piva(dati_anagrafici: ET.Element | None) -> str:
    code = _text(_path(dati_anagrafici, "IdFiscaleIVA", "IdCodice"))
    if code:
        return code
    return _text(_child(dati_anagrafici, "CodiceFiscale"))


# FatturaPA non usa mai DTD o entità: la loro presenza in un XML "fattura" da
# fonte esterna è o un errore o un tentativo di entity-expansion (billion
# laughs). Li rifiutiamo prima del parse (xml.etree espande le entità interne).
_MAX_XML_BYTES = 8 * 1024 * 1024


def parse_fattura_xml(xml_bytes: bytes) -> FatturaXML:
    """Estrae i campi essenziali da un XML FatturaPA. Solleva ``ValueError``
    se il file non è una fattura elettronica riconoscibile."""
    if len(xml_bytes) > _MAX_XML_BYTES:
        raise ValueError(f"XML troppo grande ({len(xml_bytes)} byte).")
    head = xml_bytes[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in xml_bytes.lower():
        raise ValueError("XML con DTD/entità non ammesso per una fattura elettronica.")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"XML non valido: {exc}") from exc

    if _lname(root.tag) != "FatturaElettronica":
        raise ValueError(f"Root inatteso: {_lname(root.tag)!r} (atteso FatturaElettronica).")

    header = _child(root, "FatturaElettronicaHeader")
    bodies = [c for c in list(root) if _lname(c.tag) == "FatturaElettronicaBody"]
    body = bodies[0] if bodies else None
    if header is None or body is None:
        raise ValueError("XML privo di Header o Body FatturaPA.")
    if len(bodies) > 1:
        logger.warning(
            "Fattura con %d body (lotto multi-fattura): importato solo il primo "
            "(numero %s).",
            len(bodies),
            _text(_path(bodies[0], "DatiGenerali", "DatiGeneraliDocumento", "Numero")),
        )

    ced_da = _path(header, "CedentePrestatore", "DatiAnagrafici")
    cess_da = _path(header, "CessionarioCommittente", "DatiAnagrafici")
    dgd = _path(body, "DatiGenerali", "DatiGeneraliDocumento")
    if dgd is None:
        raise ValueError("XML privo di DatiGeneraliDocumento.")

    numero = _text(_child(dgd, "Numero"))
    data_raw = _text(_child(dgd, "Data"))
    try:
        data = date.fromisoformat(data_raw[:10])
    except ValueError as exc:
        raise ValueError(f"Data documento non valida: {data_raw!r}") from exc

    dbs = _child(body, "DatiBeniServizi")
    imponibile = 0.0
    imposta = 0.0
    for riep in list(dbs) if dbs is not None else []:
        if _lname(riep.tag) != "DatiRiepilogo":
            continue
        imponibile = round(imponibile + _num(_text(_child(riep, "ImponibileImporto"))), 2)
        imposta = round(imposta + _num(_text(_child(riep, "Imposta"))), 2)

    tot_doc = _num(_text(_child(dgd, "ImportoTotaleDocumento")))
    totale = tot_doc or round(imponibile + imposta, 2)

    return FatturaXML(
        numero=numero,
        data=data,
        tipo_documento=_text(_child(dgd, "TipoDocumento")),
        cedente_piva=_piva(ced_da),
        cedente_nome=_anagrafica_nome(ced_da),
        cessionario_piva=_piva(cess_da),
        cessionario_nome=_anagrafica_nome(cess_da),
        imponibile=imponibile,
        imposta=imposta,
        totale=totale,
    )


def classifica_tipo(fx: FatturaXML, piva_azienda: str) -> str:
    """'attiva' se la carrozzeria è il cedente, 'passiva' se è il cessionario."""
    piva = (piva_azienda or "").strip()
    if piva and fx.cedente_piva.strip() == piva:
        return TIPO_ATTIVA
    if piva and fx.cessionario_piva.strip() == piva:
        return TIPO_PASSIVA
    raise ValueError(
        f"Impossibile classificare la fattura {fx.numero}: né cedente "
        f"({fx.cedente_piva}) né cessionario ({fx.cessionario_piva}) "
        f"corrispondono alla P.IVA aziendale ({piva})."
    )


# --------------------------------------------------------------------------- #
#  Import
# --------------------------------------------------------------------------- #


@dataclass
class ImportSummary:
    esaminati: int = 0
    nuove: int = 0
    duplicate: int = 0
    fuori_periodo: int = 0
    collegate_pratica: int = 0
    errori: list[str] = field(default_factory=list)


def _crea_fattura_da_xml(
    fx: FatturaXML,
    *,
    tipo: str,
    fattura_repo: ContabilitaFatturaRepository,
    stato_sdi: str,
    xml_path: str = "",
    sdi_id: str = "",
) -> tuple[Fattura, bool]:
    """Crea la riga fattura (idempotente). Ritorna (fattura, creata_ora)."""
    if tipo == TIPO_ATTIVA:
        cp_nome, cp_piva = fx.cessionario_nome, fx.cessionario_piva
    else:
        cp_nome, cp_piva = fx.cedente_nome, fx.cedente_piva

    esistente = fattura_repo.find(
        tipo=tipo, numero=fx.numero, anno=fx.anno,
        controparte_piva=cp_piva, sdi_id=sdi_id,
    )
    if esistente is not None:
        return esistente, False

    fattura = fattura_repo.create(
        tipo=tipo,
        numero=fx.numero,
        anno=fx.anno,
        data=fx.data,
        controparte_nome=cp_nome,
        controparte_piva=cp_piva,
        imponibile=fx.imponibile,
        importo_iva=fx.imposta,
        importo_totale=fx.totale,
        stato_sdi=stato_sdi,
        xml_path=xml_path,
        sdi_id=sdi_id,
        origine=ORIGINE_SDI,
    )
    return fattura, True


def _archivia_xml(archivio_dir: Path | None, anno: int, filename: str, xml_bytes: bytes) -> str:
    if archivio_dir is None:
        return ""
    # `filename` può arrivare dal JSON del provider: prendi solo il basename e
    # rifiuta valori insidiosi (path traversal / path assoluto).
    safe = Path(str(filename)).name
    if not safe or safe in (".", "..") or safe.startswith("."):
        safe = "fattura.xml"
    dest_dir = Path(archivio_dir) / str(anno)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe
    try:
        dest.write_bytes(xml_bytes)
    except OSError as exc:
        logger.warning("Archiviazione XML %s fallita: %s", filename, exc)
        return ""
    return str(dest)


def importa_attive_da_dir(
    dir_path: Path,
    *,
    piva_azienda: str,
    fattura_repo: ContabilitaFatturaRepository,
    movimento_repo: ContabilitaMovimentoRepository | None = None,
    wincar_fatture_repo=None,
    anno: int | None = None,
    since: date | None = None,
    come_storico: bool = True,
    categoria_id: int | None = None,
    categoria_nc_id: int | None = None,
    archivio_dir: Path | None = None,
) -> ImportSummary:
    """Legge gli XML delle fatture attive dalla cartella WinCar.

    - ``anno`` (se dato): importa solo le fatture con quell'anno documento.
    - ``since`` (se dato): scarta le fatture con data precedente (cutoff di
      sicurezza, tipicamente ``SDI_ATTIVE_IMPORT_SINCE``).
    - ``come_storico`` (default): la fattura è già stata trasmessa allo SDI da
      un altro canale → stato ``storico``, MAI re-inoltrata da
      ``invia_attive_pendenti``. Se ``False`` → stato ``da_inviare``.
    - ``movimento_repo`` + ``categoria_id``: se passati, crea per ogni fattura
      nuova un movimento di ricavo. ``confermato`` (entra nei report) se
      storico e con categoria; altrimenti ``proposto`` da smistare.

    Idempotente.
    """
    summary = ImportSummary()
    d = Path(dir_path)
    if not d.is_dir():
        summary.errori.append(f"Cartella non trovata: {d}")
        return summary

    stato_sdi = STATO_SDI_STORICO if come_storico else STATO_SDI_DA_INVIARE

    for xml_file in sorted(d.glob("*.xml")):
        summary.esaminati += 1
        try:
            xml_bytes = xml_file.read_bytes()
            fx = parse_fattura_xml(xml_bytes)
            tipo = classifica_tipo(fx, piva_azienda)
            if tipo != TIPO_ATTIVA:
                summary.errori.append(f"{xml_file.name}: non è una fattura attiva, saltata.")
                continue
            if anno is not None and fx.anno != int(anno):
                summary.fuori_periodo += 1
                continue
            if since is not None and fx.data < since:
                summary.fuori_periodo += 1
                continue
            xml_path = _archivia_xml(archivio_dir, fx.anno, xml_file.name, xml_bytes) or str(xml_file)
            fattura, creata = _crea_fattura_da_xml(
                fx, tipo=TIPO_ATTIVA, fattura_repo=fattura_repo,
                stato_sdi=stato_sdi, xml_path=xml_path,
            )
            if not creata:
                summary.duplicate += 1
                continue
            summary.nuove += 1
            if movimento_repo is not None:
                pratica_id = _pratica_da_wincar(wincar_fatture_repo, fx)
                mov_tipo = TIPO_USCITA if fx.is_nota_credito else TIPO_ENTRATA
                eff_cat = categoria_nc_id if fx.is_nota_credito else categoria_id
                if pratica_id is not None and eff_cat is not None:
                    # legame pieno: riga ponte + movimento confermato su pratica
                    fattura_repo.link_pratica(
                        fattura.id, pratica_id, importo_assegnato=fattura.importo_totale
                    )
                    _crea_movimento_proposto(
                        movimento_repo, fattura=fattura, tipo=mov_tipo,
                        stato=STATO_CONFERMATO, categoria_id=eff_cat,
                        pratica_id=pratica_id,
                    )
                    summary.collegate_pratica += 1
                else:
                    mov_stato = (
                        STATO_CONFERMATO
                        if (come_storico and eff_cat is not None)
                        else STATO_PROPOSTO
                    )
                    _crea_movimento_proposto(
                        movimento_repo, fattura=fattura, tipo=mov_tipo,
                        stato=mov_stato, categoria_id=eff_cat,
                    )
        except Exception as exc:  # noqa: BLE001
            summary.errori.append(f"{xml_file.name}: {exc}")
    return summary


def _pratica_da_wincar(wincar_fatture_repo, fx: FatturaXML) -> int | None:
    """Numero pratica per la fattura, letto da wcFatture.mdb. Tollera qualunque
    errore (driver Access assente, DB non raggiungibile) → None."""
    if wincar_fatture_repo is None:
        return None
    try:
        from lys_workflow_hub.core.wincar_fatture_repository import numero_fattura_int

        nf = numero_fattura_int(fx.numero)
        if nf is None:
            return None
        return wincar_fatture_repo.pratica_per_fattura(nf, fx.anno)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Lookup pratica WinCar per fattura %s fallito: %s", fx.numero, exc)
        return None


@dataclass
class CollegamentoSummary:
    esaminate: int = 0
    collegate: int = 0          # categoria + pratica
    categorizzate: int = 0      # solo categoria (nessuna pratica in WinCar)
    gia_sistemate: int = 0
    errori: list[str] = field(default_factory=list)


def _fattura_e_nota_credito_row(fattura: Fattura) -> bool:
    if fattura.xml_path and Path(fattura.xml_path).is_file():
        try:
            return parse_fattura_xml(Path(fattura.xml_path).read_bytes()).is_nota_credito
        except Exception:  # noqa: BLE001
            pass
    return False


def _fattura_da_sistemare(
    movimento_repo: ContabilitaMovimentoRepository, fattura_id: int
) -> bool:
    """True se la fattura ha ancora movimenti SDI da smistare: nessun
    movimento, oppure un movimento ``proposto``, oppure un movimento SDI
    senza categoria."""
    sdi_movs = [
        m for m in movimento_repo.list_by_fattura(fattura_id)
        if m.origine == ORIGINE_FATTURA_SDI
    ]
    if not sdi_movs:
        return True
    return any(m.stato == STATO_PROPOSTO or m.categoria_id is None for m in sdi_movs)


def collega_attive_da_wincar(
    *,
    fattura_repo: ContabilitaFatturaRepository,
    movimento_repo: ContabilitaMovimentoRepository,
    wincar_fatture_repo,
    categoria_id: int | None,
    categoria_nc_id: int | None = None,
    anno: int | None = None,
) -> CollegamentoSummary:
    """Sistema le fatture ATTIVE già importate ma non ancora attribuite:
    assegna la categoria ("Riparazioni carrozzeria", o "Note di credito" se
    l'XML è una NC) e — se il numero pratica è in ``wcFatture.mdb`` — la
    collega alla pratica. Riusa :func:`smista_fattura`. Idempotente: salta le
    fatture già legate a una pratica. Una tantum per lo storico."""
    from lys_workflow_hub.workflows.contabilita.smistamento import (
        Assegnazione,
        smista_fattura,
    )

    summary = CollegamentoSummary()
    for fattura in fattura_repo.list(tipo=TIPO_ATTIVA, anno=anno, limit=100000):
        summary.esaminate += 1
        if not _fattura_da_sistemare(movimento_repo, fattura.id):
            summary.gia_sistemate += 1
            continue
        cat = categoria_nc_id if _fattura_e_nota_credito_row(fattura) else categoria_id
        pratica_id = _pratica_da_wincar_fattura(wincar_fatture_repo, fattura)
        assegnazioni = (
            [Assegnazione(pratica_id=pratica_id, importo=fattura.importo_totale)]
            if pratica_id is not None
            else []
        )
        try:
            smista_fattura(
                fattura_repo=fattura_repo,
                movimento_repo=movimento_repo,
                fattura_id=fattura.id,
                categoria_id=cat,
                assegnazioni=assegnazioni,
            )
            if pratica_id is not None:
                summary.collegate += 1
            else:
                summary.categorizzate += 1
        except Exception as exc:  # noqa: BLE001
            summary.errori.append(f"Fattura {fattura.numero}/{fattura.anno}: {exc}")
    return summary


def _pratica_da_wincar_fattura(wincar_fatture_repo, fattura: Fattura) -> int | None:
    if wincar_fatture_repo is None:
        return None
    try:
        from lys_workflow_hub.core.wincar_fatture_repository import numero_fattura_int

        nf = numero_fattura_int(fattura.numero)
        if nf is None:
            return None
        return wincar_fatture_repo.pratica_per_fattura(nf, fattura.anno)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Lookup pratica WinCar per fattura %s/%s fallito: %s",
            fattura.numero, fattura.anno, exc,
        )
        return None


def marca_da_inviare(
    fattura_repo: ContabilitaFatturaRepository, fattura_id: int
) -> None:
    """Sposta una fattura attiva da ``storico`` a ``da_inviare`` così che il
    prossimo invio (bulk o ciclo) la trasmetta allo SDI. Usato per le poche
    fatture 2026 che NON erano state inviate da WinCar."""
    fattura = fattura_repo.get(fattura_id)
    if fattura is None or fattura.tipo != TIPO_ATTIVA:
        raise ValueError("Fattura attiva non trovata.")
    if fattura.stato_sdi in (STATO_SDI_INVIATA, STATO_SDI_SCARTATA):
        return
    fattura_repo.aggiorna_stato_sdi(fattura_id, stato_sdi=STATO_SDI_DA_INVIARE)


@dataclass
class InvioSummary:
    tentate: int = 0
    inviate: int = 0
    scartate: int = 0
    movimenti_creati: int = 0
    errori: list[str] = field(default_factory=list)


def invia_attive_pendenti(
    *,
    client: SdiClient,
    fattura_repo: ContabilitaFatturaRepository,
    movimento_repo: ContabilitaMovimentoRepository,
    anno: int | None = None,
    disabilitato: bool = False,
) -> InvioSummary:
    """Trasmette allo SDI le fatture attive in stato ``da_inviare`` e crea per
    ciascuna un movimento proposto in entrata."""
    summary = InvioSummary()
    if disabilitato:
        return summary

    for fattura in fattura_repo.list(tipo=TIPO_ATTIVA, anno=anno, limit=100000):
        if fattura.stato_sdi != STATO_SDI_DA_INVIARE:
            continue
        if not fattura.xml_path or not Path(fattura.xml_path).is_file():
            summary.errori.append(f"Fattura {fattura.numero}: XML non trovato ({fattura.xml_path}).")
            continue
        summary.tentate += 1
        payload = FatturaAttivaPayload(
            numero=fattura.numero,
            xml_bytes=Path(fattura.xml_path).read_bytes(),
            filename=Path(fattura.xml_path).name,
        )
        res = client.invia_fattura(payload)
        if not res.ok:
            summary.errori.append(f"Fattura {fattura.numero}: invio fallito ({res.messaggio}).")
            continue
        stato = STATO_SDI_SCARTATA if res.stato == "scartata" else STATO_SDI_INVIATA
        fattura_repo.aggiorna_stato_sdi(fattura.id, stato_sdi=stato, sdi_id=res.sdi_id)
        if stato == STATO_SDI_SCARTATA:
            summary.scartate += 1
            continue
        summary.inviate += 1
        _crea_movimento_proposto(
            movimento_repo,
            fattura=fattura,
            tipo=TIPO_USCITA if _fattura_e_nota_credito(fattura) else TIPO_ENTRATA,
        )
        summary.movimenti_creati += 1
    return summary


@dataclass
class SyncSummary:
    ricevute: int = 0
    nuove: int = 0
    duplicate: int = 0
    movimenti_creati: int = 0
    errori: list[str] = field(default_factory=list)


def sincronizza_passive(
    *,
    client: SdiClient,
    fattura_repo: ContabilitaFatturaRepository,
    movimento_repo: ContabilitaMovimentoRepository,
    piva_azienda: str,
    since: date | None = None,
    archivio_dir: Path | None = None,
) -> SyncSummary:
    """Scarica dallo SDI le fatture passive e crea riga + movimento proposto
    in uscita (categoria/pratica da assegnare, coda di smistamento in Fase 4)."""
    summary = SyncSummary()
    for raw in client.ricevi_fatture(since):
        summary.ricevute += 1
        try:
            fx = parse_fattura_xml(raw.xml_bytes)
        except ValueError as exc:
            summary.errori.append(f"{raw.filename}: {exc}")
            continue
        xml_path = _archivia_xml(archivio_dir, fx.anno, raw.filename, raw.xml_bytes)
        try:
            fattura, creata = _crea_fattura_da_xml(
                fx, tipo=TIPO_PASSIVA, fattura_repo=fattura_repo,
                stato_sdi=STATO_SDI_RICEVUTA, xml_path=xml_path, sdi_id=raw.sdi_id,
            )
        except Exception as exc:  # noqa: BLE001
            summary.errori.append(f"{raw.filename}: {exc}")
            continue
        if not creata:
            summary.duplicate += 1
            continue
        summary.nuove += 1
        _crea_movimento_proposto(
            movimento_repo,
            fattura=fattura,
            tipo=TIPO_ENTRATA if fx.is_nota_credito else TIPO_USCITA,
        )
        summary.movimenti_creati += 1
    return summary


def _fattura_e_nota_credito(fattura: Fattura) -> bool:
    # L'informazione TipoDocumento non è persistita sulla riga fattura; per le
    # attive la ricaviamo ri-parsando l'XML solo se serve.
    try:
        if fattura.xml_path and Path(fattura.xml_path).is_file():
            return parse_fattura_xml(Path(fattura.xml_path).read_bytes()).is_nota_credito
    except Exception:  # noqa: BLE001
        pass
    return False


def _crea_movimento_proposto(
    movimento_repo: ContabilitaMovimentoRepository,
    *,
    fattura: Fattura,
    tipo: str,
    stato: str = STATO_PROPOSTO,
    categoria_id: int | None = None,
    pratica_id: int | None = None,
) -> None:
    """Crea un movimento legato alla fattura (default: ``proposto``).

    Idempotente: se esiste già un movimento per quella fattura, non ne aggiunge.
    Con ``stato=STATO_CONFERMATO`` + ``categoria_id`` valorizzato il movimento
    entra subito nei report (usato per l'import dello storico attive)."""
    if movimento_repo.list_by_fattura(fattura.id):
        return
    verso = "a" if fattura.tipo == TIPO_ATTIVA else "da"
    descr = f"Fattura {fattura.numero}/{fattura.anno} {verso} {fattura.controparte_nome}".strip()
    movimento_repo.create(
        data=fattura.data,
        importo=fattura.importo_totale,
        tipo=tipo,
        categoria_id=categoria_id,
        pratica_id=pratica_id,
        fattura_id=fattura.id,
        descrizione=descr,
        origine=ORIGINE_FATTURA_SDI,
        stato=stato,
        importo_iva=fattura.importo_iva or None,
    )
