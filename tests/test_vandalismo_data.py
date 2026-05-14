"""Test del modello dati RichiestaVandalismoData (workflow M2)."""
from __future__ import annotations

from datetime import date

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
    RichiestaVandalismoData,
    from_pratica,
)


def _make_pratica(**overrides) -> Pratica:
    defaults = dict(
        cliente=Cliente(
            nominativo=overrides.get("nominativo", "ROSSI MARIO"),
            codice_fiscale=overrides.get("codice_fiscale", "RSSMRA80A01H501U"),
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
            data=date(2026, 5, 10),
            ora="23:30",
            comune="Roma",
            via="Via Tuscolana 100",
            dinamica="Rottura specchietto destro e graffi sulla fiancata sinistra.",
            numero=None,
            tipo="VANDALICO",
        ),
        controparte=Controparte(
            proprietario=None, conducente=None, veicolo_descrizione=None,
            targa=None, indirizzo=None, citta=None, compagnia=None,
            numero_polizza=None,
        ),
        assicurazione_cliente=CompagniaCliente(
            nome="Generali Italia S.p.A.",
            indirizzo="Piazza Tre Torri 1",
            citta="Milano",
            cap="20145",
            provincia="MI",
            numero_polizza="POL-12345",
            agenzia="Roma Centro",
        ),
    )
    defaults.update(overrides)
    return Pratica(
        numero=overrides.get("numero", 766),
        data_creazione=None,
        cliente=defaults["cliente"],
        veicolo=defaults["veicolo"],
        sinistro=defaults["sinistro"],
        controparte=defaults["controparte"],
        assicurazione_cliente=defaults["assicurazione_cliente"],
    )


# ---------------------------------------------------------------------------
# from_pratica
# ---------------------------------------------------------------------------


def test_from_pratica_estrae_dati_base_da_wincar():
    d = from_pratica(_make_pratica())
    assert d.numero_pratica == 766
    assert d.assicurato_nome_completo == "ROSSI MARIO"
    assert d.assicurato_codice_fiscale == "RSSMRA80A01H501U"
    assert d.assicurato_residenza_comune == "Roma"
    assert d.veicolo_targa == "AB123CD"
    assert d.veicolo_marca_modello == "Fiat Panda"
    assert d.evento_data == date(2026, 5, 10)
    assert d.evento_descrizione_danni.startswith("Rottura")
    assert d.polizza_compagnia_nome == "Generali Italia S.p.A."
    assert d.polizza_numero == "POL-12345"


def test_from_pratica_decodifica_il_codice_fiscale_per_nato_a():
    d = from_pratica(_make_pratica())
    assert d.assicurato_data_nascita == date(1980, 1, 1)
    assert d.assicurato_sesso == "M"
    assert d.assicurato_luogo_nascita is not None
    assert "Roma" in d.assicurato_luogo_nascita


def test_from_pratica_telefono_usa_cellulare_se_disponibile():
    p = _make_pratica()
    d = from_pratica(p)
    assert d.assicurato_telefono == "3331112233"


def test_from_pratica_riconosce_la_ditta_da_partita_iva():
    cliente = Cliente(
        nominativo="Officina Beta srl", codice_fiscale="12345678901",
        partita_iva="12345678901",
        via="Via X", citta="Roma", cap="00100", provincia="RM",
        telefono=None, cellulare=None, email=None,
    )
    p = _make_pratica(cliente=cliente)
    d = from_pratica(p)
    assert d.e_ditta is True
    assert d.ditta_nome == "Officina Beta srl"
    assert d.ditta_partita_iva == "12345678901"


def test_from_pratica_con_compagnia_anagrafica_usa_pec_e_indirizzo():
    compagnia = Compagnia(
        id=1,
        nome="Generali Italia",
        pec="atti.vandalici@pec.generali.it",
        indirizzo="Piazza Tre Torri 1",
        cap="20145",
        citta="Milano",
        provincia="MI",
        ufficio_sinistri="Ufficio Sinistri Atti Vandalici",
    )
    d = from_pratica(_make_pratica(), compagnia=compagnia)
    assert d.compagnia_pec == "atti.vandalici@pec.generali.it"
    assert d.compagnia_indirizzo == "Piazza Tre Torri 1"
    assert d.compagnia_ufficio_sinistri == "Ufficio Sinistri Atti Vandalici"


def test_overrides_hanno_precedenza_sui_valori_wincar():
    d = from_pratica(
        _make_pratica(),
        overrides={
            "evento_descrizione_danni": "Solo graffi sul cofano.",
            "denuncia_protocollo": "PROT/123/2026",
            "denuncia_data": date(2026, 5, 11),
            "compagnia_pec": "manuale@pec.test.it",
        },
    )
    assert d.evento_descrizione_danni == "Solo graffi sul cofano."
    assert d.denuncia_protocollo == "PROT/123/2026"
    assert d.denuncia_data == date(2026, 5, 11)
    assert d.compagnia_pec == "manuale@pec.test.it"


# ---------------------------------------------------------------------------
# Validazione campi mancanti
# ---------------------------------------------------------------------------


def test_campi_mancanti_segnala_tutto_su_pratica_minimale():
    pratica_minima = Pratica(
        numero=1,
        data_creazione=None,
        cliente=Cliente(
            nominativo="", codice_fiscale=None, partita_iva=None,
            via=None, citta=None, cap=None, provincia=None,
            telefono=None, cellulare=None, email=None,
        ),
        veicolo=Veicolo(targa="", marca=None, modello=None, telaio=None),
        sinistro=Sinistro(
            data=None, ora=None, comune=None, via=None,
            dinamica=None, numero=None, tipo=None,
        ),
        controparte=Controparte(
            proprietario=None, conducente=None, veicolo_descrizione=None,
            targa=None, indirizzo=None, citta=None, compagnia=None,
            numero_polizza=None,
        ),
        assicurazione_cliente=CompagniaCliente(
            nome=None, indirizzo=None, citta=None, cap=None, provincia=None,
            numero_polizza=None, agenzia=None,
        ),
    )
    d = from_pratica(pratica_minima)
    mancanti = d.campi_mancanti()
    # Deve segnalare almeno: nome, polizza, targa, evento, descrizione, denuncia.
    assert any("Nominativo" in m for m in mancanti)
    assert any("polizza" in m.lower() for m in mancanti)
    assert any("Targa" in m for m in mancanti)
    assert any("evento" in m.lower() for m in mancanti)
    assert any("Indirizzo PEC" in m for m in mancanti)


def test_campi_mancanti_vuoto_quando_tutto_compilato():
    compagnia = Compagnia(
        id=1, nome="Generali", pec="x@pec.it",
        indirizzo="A", cap="00100", citta="Roma", provincia="RM",
    )
    d = from_pratica(
        _make_pratica(),
        compagnia=compagnia,
        overrides={
            "denuncia_data": date(2026, 5, 11),
            "denuncia_autorita": "Carabinieri",
        },
    )
    assert d.campi_mancanti() == []


# ---------------------------------------------------------------------------
# Proprietà calcolate
# ---------------------------------------------------------------------------


def test_residenza_compatta_formatta_via_cap_citta_provincia():
    d = from_pratica(_make_pratica())
    res = d.residenza_compatta
    assert "Via Tuscolana 100" in res
    assert "Roma" in res
    assert "00181" in res
    assert "(RM)" in res


def test_sottoscritto_label_dipende_dal_sesso():
    d_m = from_pratica(_make_pratica(), overrides={"assicurato_sesso": "M"})
    d_f = from_pratica(_make_pratica(), overrides={"assicurato_sesso": "F"})
    assert d_m.sottoscritto_label == "Il sottoscritto"
    assert d_f.sottoscritto_label == "La sottoscritta"
