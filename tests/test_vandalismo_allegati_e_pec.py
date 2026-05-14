"""Test dello scanner allegati e del generatore di testo PEC (M2)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lys_workflow_hub.core.compagnie_repository import Compagnia
from lys_workflow_hub.core.wincar_repository import (
    Cliente,
    CompagniaCliente,
    Controparte,
    Pratica,
    Sinistro,
    Veicolo,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo import (
    build_body,
    build_subject,
    from_pratica,
    scan,
    selezione_nomi_default,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.allegati import (
    AllegatiPratica,
    cartella_allegati,
    cartella_foto,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_pratica() -> Pratica:
    return Pratica(
        numero=766,
        data_creazione=None,
        cliente=Cliente(
            nominativo="ROSSI MARIO",
            codice_fiscale="RSSMRA80A01H501U",
            partita_iva=None,
            via="Via Tuscolana 100",
            citta="Roma",
            cap="00181",
            provincia="RM",
            telefono=None,
            cellulare="3331112233",
            email="mario.rossi@example.com",
        ),
        veicolo=Veicolo(targa="AB123CD", marca="Fiat", modello="Panda", telaio="ZFA12300000123456"),
        sinistro=Sinistro(
            data=date(2026, 5, 10), ora="23:30",
            comune="Roma", via="Via Tuscolana 100",
            dinamica="Rottura specchietto destro e graffi sulla fiancata sinistra.",
            numero=None, tipo="VANDALICO",
        ),
        controparte=Controparte(
            proprietario=None, conducente=None, veicolo_descrizione=None,
            targa=None, indirizzo=None, citta=None, compagnia=None,
            numero_polizza=None,
        ),
        assicurazione_cliente=CompagniaCliente(
            nome="Generali Italia S.p.A.",
            indirizzo="Piazza Tre Torri 1",
            citta="Milano", cap="20145", provincia="MI",
            numero_polizza="POL-12345", agenzia="Roma Centro",
        ),
    )


def _crea_file(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# Scanner allegati
# ---------------------------------------------------------------------------


def test_scan_classifica_foto_dalla_cartella_foto(tmp_path: Path):
    foto_dir = cartella_foto(tmp_path, 766)
    _crea_file(foto_dir / "IMG_001.jpg")
    _crea_file(foto_dir / "IMG_002.JPG")
    _crea_file(foto_dir / "danno_laterale.png")
    risultato = scan(tmp_path, 766)
    assert {a.nome_file for a in risultato.foto} == {
        "IMG_001.jpg", "IMG_002.JPG", "danno_laterale.png"
    }
    assert risultato.conteggio_foto == 3


def test_scan_classifica_documenti_dalla_cartella_allegati(tmp_path: Path):
    alleg_dir = cartella_allegati(tmp_path, 766)
    _crea_file(alleg_dir / "Cessione_credito_20260513_firmata.pdf")
    _crea_file(alleg_dir / "denuncia_carabinieri.pdf")
    _crea_file(alleg_dir / "verbale_polizia.pdf")
    _crea_file(alleg_dir / "patente_assicurato.pdf")
    _crea_file(alleg_dir / "libretto_circolazione.pdf")

    risultato = scan(tmp_path, 766)
    assert len(risultato.cessioni) == 1
    assert risultato.cessioni[0].nome_file == "Cessione_credito_20260513_firmata.pdf"
    nomi_denunce = {a.nome_file for a in risultato.denunce}
    assert "denuncia_carabinieri.pdf" in nomi_denunce
    assert "verbale_polizia.pdf" in nomi_denunce
    nomi_altri = {a.nome_file for a in risultato.altri}
    assert "patente_assicurato.pdf" in nomi_altri


def test_scan_ignora_backup_automatici(tmp_path: Path):
    alleg_dir = cartella_allegati(tmp_path, 766)
    _crea_file(alleg_dir / "Cessione_credito_20260513_firmata.pdf")
    _crea_file(alleg_dir / "Cessione_credito_20260513_firmata.backup-1234567.pdf")
    risultato = scan(tmp_path, 766)
    nomi = {a.nome_file for a in risultato.tutti}
    assert "Cessione_credito_20260513_firmata.pdf" in nomi
    assert "Cessione_credito_20260513_firmata.backup-1234567.pdf" not in nomi


def test_scan_su_pratica_senza_cartelle_restituisce_struttura_vuota(tmp_path: Path):
    risultato = scan(tmp_path, 9999)
    assert risultato.tutti == []
    assert risultato.ha_cessione is False
    assert risultato.ha_denuncia is False


def test_scan_esclude_miniature_thumb_e_cache_di_sistema(tmp_path: Path):
    """File `.thumb`, `Thumbs.db`, `desktop.ini` non devono comparire."""
    foto_dir = cartella_foto(tmp_path, 766)
    alleg_dir = cartella_allegati(tmp_path, 766)
    # File legittimi
    _crea_file(foto_dir / "IMG_001.jpg")
    _crea_file(foto_dir / "IMG_002.PNG")
    _crea_file(alleg_dir / "denuncia.pdf")
    # Rumore da escludere
    _crea_file(foto_dir / "IMG_001.jpg.thumb")
    _crea_file(foto_dir / "IMG_002.thumb")
    _crea_file(foto_dir / "Thumbs.db")
    _crea_file(foto_dir / "desktop.ini")
    _crea_file(alleg_dir / "Thumbs.db")
    _crea_file(alleg_dir / ".DS_Store")

    risultato = scan(tmp_path, 766)
    nomi = {a.nome_file for a in risultato.tutti}
    # Presenti
    assert "IMG_001.jpg" in nomi
    assert "IMG_002.PNG" in nomi
    assert "denuncia.pdf" in nomi
    # Assenti
    assert "IMG_001.jpg.thumb" not in nomi
    assert "IMG_002.thumb" not in nomi
    assert "Thumbs.db" not in nomi
    assert "desktop.ini" not in nomi
    assert ".DS_Store" not in nomi


def test_scan_foto_finite_in_allegati_vengono_riconosciute(tmp_path: Path):
    """Se per errore una foto viene salvata in Allegati/, comunque la riconosciamo."""
    alleg_dir = cartella_allegati(tmp_path, 766)
    _crea_file(alleg_dir / "danno_extra.jpg")
    risultato = scan(tmp_path, 766)
    assert any(a.nome_file == "danno_extra.jpg" for a in risultato.foto)


def test_selezione_default_include_cessione_e_denunce_e_foto(tmp_path: Path):
    foto_dir = cartella_foto(tmp_path, 766)
    alleg_dir = cartella_allegati(tmp_path, 766)
    _crea_file(foto_dir / "IMG_001.jpg")
    _crea_file(alleg_dir / "Cessione_credito_20260513_firmata.pdf")
    _crea_file(alleg_dir / "denuncia.pdf")
    _crea_file(alleg_dir / "patente.pdf")
    risultato = scan(tmp_path, 766)
    selezione = set(selezione_nomi_default(risultato))
    assert "IMG_001.jpg" in selezione
    assert "Cessione_credito_20260513_firmata.pdf" in selezione
    assert "denuncia.pdf" in selezione
    assert "patente.pdf" not in selezione  # gli "altri" non sono selezionati di default


# ---------------------------------------------------------------------------
# Generatore PEC
# ---------------------------------------------------------------------------


def test_subject_include_targa_e_polizza():
    d = from_pratica(_make_pratica())
    s = build_subject(d)
    assert "AB123CD" in s
    assert "POL-12345" in s
    assert "atti vandalici" in s.lower()


def test_body_contiene_tutti_i_dati_chiave():
    compagnia = Compagnia(
        id=1, nome="Generali Italia", pec="atti.vandalici@pec.generali.it",
        indirizzo="Piazza Tre Torri 1", cap="20145", citta="Milano", provincia="MI",
        ufficio_sinistri="Ufficio Sinistri Atti Vandalici",
    )
    d = from_pratica(
        _make_pratica(),
        compagnia=compagnia,
        carrozzeria_pec="lysauto@pec.it",
        carrozzeria_email="info@lysauto.it",
        carrozzeria_telefono="06 0000000",
        overrides={
            "denuncia_data": date(2026, 5, 11),
            "denuncia_protocollo": "PROT/123/2026",
            "denuncia_comando": "Stazione Carabinieri Roma Tomba di Nerone",
        },
    )
    body = build_body(d)

    # Destinatario
    assert "Generali Italia" in body
    assert "atti.vandalici@pec.generali.it" in body
    assert "Ufficio Sinistri Atti Vandalici" in body

    # Assicurato
    assert "ROSSI MARIO" in body
    assert "RSSMRA80A01H501U" in body
    assert "Via Tuscolana 100" in body
    assert "Roma" in body

    # Polizza + veicolo
    assert "POL-12345" in body
    assert "AB123CD" in body
    assert "Fiat Panda" in body

    # Evento + denuncia
    assert "10/05/2026" in body  # evento
    assert "11/05/2026" in body  # denuncia
    assert "PROT/123/2026" in body
    assert "Carabinieri" in body
    assert "Rottura specchietto destro" in body

    # Cessione
    assert "Cessione" in body or "cessione" in body
    assert "LYS Auto" in body

    # Contatti carrozzeria
    assert "lysauto@pec.it" in body
    assert "06 0000000" in body


def test_body_su_campi_mancanti_usa_placeholder():
    """Quando un campo cruciale è vuoto, deve apparire un placeholder visibile."""
    pratica = _make_pratica()
    d = from_pratica(
        pratica,
        overrides={
            "polizza_numero": "",
            "veicolo_targa": "",
        },
    )
    body = build_body(d)
    # Il placeholder concordato è "________"
    assert "________" in body


def test_body_elenca_allegati_passati(tmp_path: Path):
    foto_dir = cartella_foto(tmp_path, 766)
    alleg_dir = cartella_allegati(tmp_path, 766)
    _crea_file(foto_dir / "IMG_001.jpg")
    _crea_file(foto_dir / "IMG_002.jpg")
    _crea_file(alleg_dir / "Cessione_credito_20260513_firmata.pdf")
    _crea_file(alleg_dir / "denuncia.pdf")
    allegati = scan(tmp_path, 766)

    d = from_pratica(_make_pratica())
    body = build_body(d, allegati=allegati)

    assert "denuncia.pdf" in body
    assert "Cessione_credito_20260513_firmata.pdf" in body
    assert "IMG_001.jpg" in body
    assert "IMG_002.jpg" in body
    # Indica anche il conteggio foto.
    assert "2 immagini" in body


def test_body_senza_allegati_usa_fallback_generico():
    d = from_pratica(_make_pratica())
    body = build_body(d, allegati=None)
    assert "vedere file allegati" in body.lower()
