"""Smoke test delle pagine HTML della contabilità gestionale (Fase 1)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes_contabilita import get_contabilita_settings
from tests.conftest import get_csrf, login_as_admin


@pytest.fixture
def client(tmp_path: Path, authenticated_app):
    settings = Settings(
        wincar_archivio=tmp_path,
        app_db_path=tmp_path / "app.db",
        sdi_wincar_attive_dir=tmp_path / "wincar_attive",
        app_archivio_fatture=tmp_path / "archivio_fatture",
    )
    app.dependency_overrides[get_contabilita_settings] = lambda: settings
    c = TestClient(app, follow_redirects=False)
    login_as_admin(c)
    try:
        yield c, settings
    finally:
        app.dependency_overrides.pop(get_contabilita_settings, None)


def test_home_redirige_ai_movimenti(client):
    c, _ = client
    resp = c.get("/contabilita")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/contabilita/movimenti"


def test_lista_movimenti_vuota(client):
    c, _ = client
    resp = c.get("/contabilita/movimenti")
    assert resp.status_code == 200
    assert "Movimenti" in resp.text
    assert "Nessun movimento" in resp.text


def test_categorie_seed_visibili(client):
    c, _ = client
    resp = c.get("/contabilita/categorie")
    assert resp.status_code == 200
    assert "Ricambi" in resp.text
    assert "Manodopera" in resp.text


def test_crea_movimento_manuale(client):
    c, settings = client
    token = get_csrf(c, "/contabilita/movimenti/nuovo")
    resp = c.post(
        "/contabilita/movimenti/nuovo",
        data={
            "csrf_token": token,
            "data": "2026-05-10",
            "importo": "1234,56",
            "tipo": "uscita",
            "categoria_id": "",
            "pratica_id": "766",
            "descrizione": "Stipendio maggio",
            "importo_iva": "",
            "stato": "confermato",
        },
    )
    assert resp.status_code == 303
    repo = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    movimenti = repo.list()
    assert len(movimenti) == 1
    assert movimenti[0].importo == 1234.56
    assert movimenti[0].pratica_id == 766
    assert movimenti[0].descrizione == "Stipendio maggio"


def test_crea_movimento_dati_invalidi_rimostra_form(client):
    c, settings = client
    token = get_csrf(c, "/contabilita/movimenti/nuovo")
    resp = c.post(
        "/contabilita/movimenti/nuovo",
        data={
            "csrf_token": token,
            "data": "10/05/2026",  # formato sbagliato
            "importo": "100",
            "tipo": "uscita",
            "stato": "confermato",
        },
    )
    assert resp.status_code == 200
    assert "Errore" in resp.text
    assert ContabilitaMovimentoRepository(db_path=settings.app_db_path).list() == []


def test_lista_filtra_per_pratica(client):
    c, settings = client
    repo = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    repo.create(data="2026-01-10", importo="100", tipo="uscita", pratica_id=10)
    repo.create(data="2026-01-11", importo="200", tipo="entrata", pratica_id=20)

    resp = c.get("/contabilita/movimenti?pratica_id=10")
    assert resp.status_code == 200
    assert "€ 100.00" in resp.text
    assert "€ 200.00" not in resp.text


def test_crea_categoria(client):
    c, settings = client
    from lys_workflow_hub.core.contabilita_categoria_repository import (
        ContabilitaCategoriaRepository,
    )

    token = get_csrf(c, "/contabilita/categorie")
    resp = c.post(
        "/contabilita/categorie/nuova",
        data={"csrf_token": token, "nome": "Leasing furgone", "tipo": "costo"},
    )
    assert resp.status_code == 303
    nomi = {x.nome for x in ContabilitaCategoriaRepository(db_path=settings.app_db_path).list_all()}
    assert "Leasing furgone" in nomi


def test_route_riservata_ad_admin(tmp_path: Path, authenticated_app):
    """Senza login → redirect a /login (AuthMiddleware)."""
    c = TestClient(app, follow_redirects=False)
    resp = c.get("/contabilita/movimenti")
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


# --------------------------------------------------------------------------- #
#  Fatture SDI (Fase 3)
# --------------------------------------------------------------------------- #


def test_fatture_list_vuota(client):
    c, _ = client
    resp = c.get("/contabilita/fatture")
    assert resp.status_code == 200
    assert "Fatture elettroniche" in resp.text
    assert "Nessuna fattura registrata" in resp.text


def test_fatture_importa_attive_dir_mancante(client):
    c, _ = client
    token = get_csrf(c, "/contabilita/fatture")
    resp = c.post(
        "/contabilita/fatture/importa-attive", data={"csrf_token": token}
    )
    assert resp.status_code == 303
    assert "esito=" in resp.headers["location"]


def _scrivi_xml_attiva(settings, numero="10", data="2026-05-01"):
    d = settings.sdi_wincar_attive_dir
    d.mkdir(parents=True, exist_ok=True)
    xml = (
        '<?xml version="1.0"?><p:FatturaElettronica '
        'xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2">'
        "<FatturaElettronicaHeader><CedentePrestatore><DatiAnagrafici>"
        "<IdFiscaleIVA><IdCodice>14521721002</IdCodice></IdFiscaleIVA>"
        "<Anagrafica><Denominazione>LYS AUTO SRL</Denominazione></Anagrafica>"
        "</DatiAnagrafici></CedentePrestatore><CessionarioCommittente><DatiAnagrafici>"
        "<IdFiscaleIVA><IdCodice>09876543210</IdCodice></IdFiscaleIVA>"
        "<Anagrafica><Denominazione>ROSSI MARIO</Denominazione></Anagrafica>"
        "</DatiAnagrafici></CessionarioCommittente></FatturaElettronicaHeader>"
        "<FatturaElettronicaBody><DatiGenerali><DatiGeneraliDocumento>"
        f"<TipoDocumento>TD01</TipoDocumento><Data>{data}</Data><Numero>{numero}</Numero>"
        "<ImportoTotaleDocumento>1220.00</ImportoTotaleDocumento>"
        "</DatiGeneraliDocumento></DatiGenerali><DatiBeniServizi><DatiRiepilogo>"
        "<ImponibileImporto>1000.00</ImponibileImporto><Imposta>220.00</Imposta>"
        "</DatiRiepilogo></DatiBeniServizi></FatturaElettronicaBody></p:FatturaElettronica>"
    )
    (d / f"IT_{numero}.xml").write_text(xml, encoding="utf-8")


def test_fatture_importa_attive_come_storico_e_segna_da_inviare(client):
    c, settings = client
    _scrivi_xml_attiva(settings, numero="500", data="2026-06-01")
    _scrivi_xml_attiva(settings, numero="499", data="2025-06-01")  # fuori periodo

    token = get_csrf(c, "/contabilita/fatture")
    resp = c.post("/contabilita/fatture/importa-attive", data={
        "csrf_token": token, "anno": "2026", "categoria_id": "", "come_storico": "1",
    })
    assert resp.status_code == 303

    from lys_workflow_hub.core.contabilita_fattura_repository import (
        ContabilitaFatturaRepository,
    )
    fat = ContabilitaFatturaRepository(db_path=settings.app_db_path)
    attive = fat.list(tipo="attiva")
    assert {f.numero for f in attive} == {"500"}  # 2025 esclusa dal cutoff
    fid = attive[0].id
    assert fat.get(fid).stato_sdi == "storico"

    # storico → invio bulk non la tocca
    token = get_csrf(c, "/contabilita/fatture")
    c.post("/contabilita/fatture/invia-sdi", data={"csrf_token": token})
    assert fat.get(fid).stato_sdi == "storico"

    # segna da inviare
    token = get_csrf(c, "/contabilita/fatture")
    resp = c.post(f"/contabilita/fatture/{fid}/segna-da-inviare", data={"csrf_token": token})
    assert resp.status_code == 303
    assert fat.get(fid).stato_sdi == "da_inviare"


def test_fatture_invia_sdi_nessuna_pendente(client):
    c, _ = client
    token = get_csrf(c, "/contabilita/fatture")
    resp = c.post("/contabilita/fatture/invia-sdi", data={"csrf_token": token})
    assert resp.status_code == 303
    resp2 = c.get(resp.headers["location"], follow_redirects=False)
    assert resp2.status_code == 200


def test_fatture_sincronizza_passive_fake_provider(client):
    c, settings = client
    # provider di default in test = "fake" → inbox vuota, nessun errore
    assert settings.sdi_provider == "fake"
    token = get_csrf(c, "/contabilita/fatture")
    resp = c.post(
        "/contabilita/fatture/sincronizza-passive", data={"csrf_token": token}
    )
    assert resp.status_code == 303
    assert "/contabilita/fatture?esito=" in resp.headers["location"]
    assert "passive" in resp.headers["location"]


# --------------------------------------------------------------------------- #
#  Smistamento + report (Fase 4)
# --------------------------------------------------------------------------- #


def _fattura_passiva_proposta(settings):
    from lys_workflow_hub.core.contabilita_fattura_repository import (
        ContabilitaFatturaRepository,
    )
    from lys_workflow_hub.core.contabilita_movimento_repository import (
        ContabilitaMovimentoRepository,
    )

    fat = ContabilitaFatturaRepository(db_path=settings.app_db_path)
    mov = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    f = fat.create(
        tipo="passiva", numero="F-77", anno=2026, data="2026-04-01",
        controparte_nome="Fornitore X", controparte_piva="01234567890",
        importo_totale="1220",
    )
    mov.create(
        data="2026-04-01", importo="1220", tipo="uscita", fattura_id=f.id,
        origine="da_fattura_sdi", stato="proposto",
    )
    return f


def test_coda_smistamento_vuota(client):
    c, _ = client
    resp = c.get("/contabilita/fatture/passive/da-collegare")
    assert resp.status_code == 200
    assert "Niente da smistare" in resp.text


def test_smista_fattura_flow(client):
    c, settings = client
    f = _fattura_passiva_proposta(settings)

    resp = c.get("/contabilita/fatture/passive/da-collegare")
    assert "F-77/2026" in resp.text

    form = c.get(f"/contabilita/fatture/{f.id}/smista")
    assert form.status_code == 200
    token = get_csrf(c, f"/contabilita/fatture/{f.id}/smista")

    from lys_workflow_hub.core.contabilita_categoria_repository import (
        ContabilitaCategoriaRepository,
    )
    cat = ContabilitaCategoriaRepository(db_path=settings.app_db_path)
    ricambi = next(x for x in cat.list_all() if x.nome == "Ricambi")

    resp = c.post(
        f"/contabilita/fatture/{f.id}/smista",
        data={
            "csrf_token": token,
            "categoria_id": str(ricambi.id),
            "pratica_id": "766",
            "importo": "1220",
        },
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/contabilita/fatture/passive/da-collegare"

    from lys_workflow_hub.core.contabilita_movimento_repository import (
        ContabilitaMovimentoRepository,
    )
    mov = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    movimenti = mov.list_by_fattura(f.id)
    assert len(movimenti) == 1
    assert movimenti[0].stato == "confermato"
    assert movimenti[0].pratica_id == 766
    assert movimenti[0].categoria_id == ricambi.id


def test_smista_somma_eccedente_mostra_errore(client):
    c, settings = client
    f = _fattura_passiva_proposta(settings)
    token = get_csrf(c, f"/contabilita/fatture/{f.id}/smista")
    resp = c.post(
        f"/contabilita/fatture/{f.id}/smista",
        data={"csrf_token": token, "categoria_id": "",
              "pratica_id": "1", "importo": "5000"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "supera" in resp.text


def test_report_dashboard(client):
    c, settings = client
    from lys_workflow_hub.core.contabilita_movimento_repository import (
        ContabilitaMovimentoRepository,
    )
    mov = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    mov.create(data="2026-03-01", importo="1000", tipo="entrata")
    mov.create(data="2026-03-02", importo="400", tipo="uscita")

    resp = c.get("/contabilita/report")
    assert resp.status_code == 200
    assert "Report costi / ricavi" in resp.text
    assert "€ 1000.00" in resp.text
    assert "€ 600.00" in resp.text  # margine


# --------------------------------------------------------------------------- #
#  Costi ricorrenti (Fase 5)
# --------------------------------------------------------------------------- #


def test_ricorrenti_crud_e_genera(client):
    c, settings = client
    from lys_workflow_hub.core.contabilita_costo_ricorrente_repository import (
        ContabilitaCostoRicorrenteRepository,
    )
    from lys_workflow_hub.core.contabilita_movimento_repository import (
        ContabilitaMovimentoRepository,
    )

    resp = c.get("/contabilita/ricorrenti")
    assert resp.status_code == 200
    assert "Costi ricorrenti" in resp.text

    token = get_csrf(c, "/contabilita/ricorrenti")
    resp = c.post("/contabilita/ricorrenti/nuovo", data={
        "csrf_token": token, "nome": "Autolavaggi", "categoria_id": "",
        "importo": "150", "cadenza": "mensile", "giorno_mese": "5",
        "data_inizio": "2026-01-05", "descrizione": "",
    })
    assert resp.status_code == 303
    ric = ContabilitaCostoRicorrenteRepository(db_path=settings.app_db_path)
    assert [x.nome for x in ric.list_all()] == ["Autolavaggi"]

    # genera (fino a oggi reale — almeno i mesi 2026 passati)
    token = get_csrf(c, "/contabilita/ricorrenti")
    resp = c.post("/contabilita/ricorrenti/genera", data={"csrf_token": token})
    assert resp.status_code == 303
    mov = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    m = mov.list(stato="confermato")
    assert m and all(x.origine == "ricorrente" and x.tipo == "uscita" for x in m)


def test_ricorrente_elimina_rimuove_anche_i_movimenti(client):
    c, settings = client
    from lys_workflow_hub.core.contabilita_costo_ricorrente_repository import (
        ContabilitaCostoRicorrenteRepository,
    )
    from lys_workflow_hub.core.contabilita_movimento_repository import (
        ContabilitaMovimentoRepository,
    )

    ric = ContabilitaCostoRicorrenteRepository(db_path=settings.app_db_path)
    r = ric.create(nome="Affitto", categoria_id=None, importo="1200",
                   cadenza="mensile", giorno_mese="1", data_inizio="2026-01-01")
    mov = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    mov.create(data="2026-01-02", importo="99", tipo="uscita")  # manuale, resta
    token = get_csrf(c, "/contabilita/ricorrenti")
    c.post("/contabilita/ricorrenti/genera", data={"csrf_token": token})
    assert len(mov.list()) > 1

    token = get_csrf(c, "/contabilita/ricorrenti")
    resp = c.post(f"/contabilita/ricorrenti/{r.id}/elimina", data={"csrf_token": token})
    assert resp.status_code == 303
    assert ric.get(r.id) is None
    resto = mov.list()
    assert len(resto) == 1 and resto[0].origine == "manuale"


def test_ricorrente_modifica_azzera_e_rigenera(client):
    c, settings = client
    from lys_workflow_hub.core.contabilita_costo_ricorrente_repository import (
        ContabilitaCostoRicorrenteRepository,
    )
    from lys_workflow_hub.core.contabilita_movimento_repository import (
        ContabilitaMovimentoRepository,
    )

    ric = ContabilitaCostoRicorrenteRepository(db_path=settings.app_db_path)
    r = ric.create(nome="Affitto", categoria_id=None, importo="1000",
                   cadenza="mensile", giorno_mese="1", data_inizio="2026-01-01")
    token = get_csrf(c, "/contabilita/ricorrenti")
    c.post("/contabilita/ricorrenti/genera", data={"csrf_token": token})
    mov = ContabilitaMovimentoRepository(db_path=settings.app_db_path)
    assert all(m.importo == 1000.0 for m in mov.list())

    # modifica importo → i vecchi movimenti spariscono, watermark azzerato
    token = get_csrf(c, "/contabilita/ricorrenti")
    resp = c.post(f"/contabilita/ricorrenti/{r.id}/modifica", data={
        "csrf_token": token, "nome": "Affitto", "categoria_id": "",
        "importo": "1300", "cadenza": "mensile", "giorno_mese": "1",
        "data_inizio": "2026-01-01", "descrizione": "", "attivo": "1",
    })
    assert resp.status_code == 303
    assert mov.list() == []
    assert ric.get(r.id).ultimo_periodo is None

    # rigenera → nuovi valori
    token = get_csrf(c, "/contabilita/ricorrenti")
    c.post("/contabilita/ricorrenti/genera", data={"csrf_token": token})
    nuovi = mov.list()
    assert nuovi and all(m.importo == 1300.0 for m in nuovi)
