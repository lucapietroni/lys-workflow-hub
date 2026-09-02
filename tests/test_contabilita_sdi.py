"""Test integrazione SDI (Fase 3): parser XML FatturaPA + import/invio/sync."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lys_workflow_hub.core.contabilita_fattura_repository import (
    ContabilitaFatturaRepository,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.integrations.sdi import (
    FakeSdiClient,
    FatturaPassivaRaw,
    build_sdi_client,
)
from lys_workflow_hub.workflows.contabilita.sdi_import import (
    STATO_SDI_DA_INVIARE,
    STATO_SDI_INVIATA,
    classifica_tipo,
    importa_attive_da_dir,
    invia_attive_pendenti,
    parse_fattura_xml,
    sincronizza_passive,
)

PIVA_LYS = "14521721002"
PIVA_CLIENTE = "09876543210"
PIVA_FORNITORE = "01112223330"


def _xml(
    *,
    ced_piva=PIVA_LYS,
    ced_nome="LYS AUTO SRL",
    cess_piva=PIVA_CLIENTE,
    cess_nome="ROSSI MARIO",
    numero="123",
    data="2026-05-10",
    tipo_doc="TD01",
    imponibile="1000.00",
    imposta="220.00",
    totale="1220.00",
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<p:FatturaElettronica versione="FPR12"
   xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2">
  <FatturaElettronicaHeader>
    <CedentePrestatore><DatiAnagrafici>
      <IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>{ced_piva}</IdCodice></IdFiscaleIVA>
      <Anagrafica><Denominazione>{ced_nome}</Denominazione></Anagrafica>
    </DatiAnagrafici></CedentePrestatore>
    <CessionarioCommittente><DatiAnagrafici>
      <IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>{cess_piva}</IdCodice></IdFiscaleIVA>
      <Anagrafica><Denominazione>{cess_nome}</Denominazione></Anagrafica>
    </DatiAnagrafici></CessionarioCommittente>
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali><DatiGeneraliDocumento>
      <TipoDocumento>{tipo_doc}</TipoDocumento>
      <Divisa>EUR</Divisa>
      <Data>{data}</Data>
      <Numero>{numero}</Numero>
      <ImportoTotaleDocumento>{totale}</ImportoTotaleDocumento>
    </DatiGeneraliDocumento></DatiGenerali>
    <DatiBeniServizi><DatiRiepilogo>
      <AliquotaIVA>22.00</AliquotaIVA>
      <ImponibileImporto>{imponibile}</ImponibileImporto>
      <Imposta>{imposta}</Imposta>
    </DatiRiepilogo></DatiBeniServizi>
  </FatturaElettronicaBody>
</p:FatturaElettronica>""".encode()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "app.db"
    ContabilitaFatturaRepository(db_path=p)
    ContabilitaMovimentoRepository(db_path=p)
    return p


# --------------------------------------------------------------------------- parser


def test_parse_fattura_xml():
    fx = parse_fattura_xml(_xml())
    assert fx.numero == "123"
    assert fx.data == date(2026, 5, 10)
    assert fx.anno == 2026
    assert fx.cedente_piva == PIVA_LYS
    assert fx.cedente_nome == "LYS AUTO SRL"
    assert fx.cessionario_piva == PIVA_CLIENTE
    assert fx.imponibile == 1000.0
    assert fx.imposta == 220.0
    assert fx.totale == 1220.0
    assert fx.is_nota_credito is False


def test_parse_totale_calcolato_se_assente():
    xml = _xml().replace(
        b"<ImportoTotaleDocumento>1220.00</ImportoTotaleDocumento>", b""
    )
    fx = parse_fattura_xml(xml)
    assert fx.totale == 1220.0  # imponibile 1000 + imposta 220


def test_parse_xml_non_fattura():
    with pytest.raises(ValueError):
        parse_fattura_xml(b"<html><body>ciao</body></html>")


def test_classifica_tipo():
    attiva = parse_fattura_xml(_xml(ced_piva=PIVA_LYS, cess_piva=PIVA_CLIENTE))
    passiva = parse_fattura_xml(_xml(ced_piva=PIVA_FORNITORE, cess_piva=PIVA_LYS))
    assert classifica_tipo(attiva, PIVA_LYS) == "attiva"
    assert classifica_tipo(passiva, PIVA_LYS) == "passiva"
    with pytest.raises(ValueError):
        classifica_tipo(attiva, "00000000000")


# --------------------------------------------------------------------------- client


def test_build_sdi_client_default_fake():
    class S:
        sdi_provider = "fake"
    assert isinstance(build_sdi_client(S()), FakeSdiClient)


def test_fake_client_invio():
    c = FakeSdiClient()
    from lys_workflow_hub.integrations.sdi import FatturaAttivaPayload

    res = c.invia_fattura(FatturaAttivaPayload(numero="7", xml_bytes=b"<x/>", filename="7.xml"))
    assert res.ok and res.sdi_id == "FAKE-7"
    assert len(c.inviate) == 1


# --------------------------------------------------------------------------- import attive


def test_importa_attive_da_dir_idempotente(db: Path, tmp_path: Path):
    src = tmp_path / "wincar_attive"
    src.mkdir()
    (src / "IT_00001.xml").write_bytes(_xml(numero="1"))
    (src / "IT_00002.xml").write_bytes(_xml(numero="2"))
    fat = ContabilitaFatturaRepository(db_path=db)

    s1 = importa_attive_da_dir(src, piva_azienda=PIVA_LYS, fattura_repo=fat)
    assert (s1.esaminati, s1.nuove, s1.duplicate) == (2, 2, 0)
    fatture = fat.list(tipo="attiva")
    assert {f.numero for f in fatture} == {"1", "2"}
    assert all(f.stato_sdi == STATO_SDI_DA_INVIARE for f in fatture)

    s2 = importa_attive_da_dir(src, piva_azienda=PIVA_LYS, fattura_repo=fat)
    assert (s2.nuove, s2.duplicate) == (0, 2)
    assert len(fat.list(tipo="attiva")) == 2


def test_importa_attive_dir_mancante(db: Path, tmp_path: Path):
    s = importa_attive_da_dir(tmp_path / "nope", piva_azienda=PIVA_LYS,
                              fattura_repo=ContabilitaFatturaRepository(db_path=db))
    assert s.esaminati == 0
    assert s.errori


# --------------------------------------------------------------------------- invio


def test_invia_attive_pendenti_crea_movimento_proposto(db: Path, tmp_path: Path):
    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="55", totale="1220.00"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    importa_attive_da_dir(src, piva_azienda=PIVA_LYS, fattura_repo=fat)

    client = FakeSdiClient()
    s = invia_attive_pendenti(client=client, fattura_repo=fat, movimento_repo=mov)
    assert s.inviate == 1
    assert s.movimenti_creati == 1
    f = fat.list(tipo="attiva")[0]
    assert f.stato_sdi == STATO_SDI_INVIATA
    m = mov.list_by_fattura(f.id)[0]
    assert m.tipo == "entrata"
    assert m.stato == "proposto"
    assert m.importo == 1220.0

    # idempotente: seconda passata non reinvia né duplica il movimento
    s2 = invia_attive_pendenti(client=client, fattura_repo=fat, movimento_repo=mov)
    assert s2.inviate == 0
    assert len(mov.list_by_fattura(f.id)) == 1


def test_invia_disabilitato_non_fa_nulla(db: Path, tmp_path: Path):
    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="1"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    importa_attive_da_dir(src, piva_azienda=PIVA_LYS, fattura_repo=fat)
    s = invia_attive_pendenti(client=FakeSdiClient(), fattura_repo=fat,
                              movimento_repo=mov, disabilitato=True)
    assert s.tentate == 0
    assert fat.list(tipo="attiva")[0].stato_sdi == STATO_SDI_DA_INVIARE


# --------------------------------------------------------------------------- passive


def test_sincronizza_passive(db: Path):
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    client = FakeSdiClient(inbox=[
        FatturaPassivaRaw(
            sdi_id="SDI-1",
            xml_bytes=_xml(ced_piva=PIVA_FORNITORE, ced_nome="RICAMBI SPA",
                           cess_piva=PIVA_LYS, numero="F-9", totale="610.00",
                           imponibile="500.00", imposta="110.00"),
            filename="F-9.xml",
        ),
    ])
    s = sincronizza_passive(client=client, fattura_repo=fat, movimento_repo=mov,
                            piva_azienda=PIVA_LYS)
    assert (s.ricevute, s.nuove, s.movimenti_creati) == (1, 1, 1)
    f = fat.list(tipo="passiva")[0]
    assert f.controparte_nome == "RICAMBI SPA"
    assert f.stato_sdi == "ricevuta"
    m = mov.list_by_fattura(f.id)[0]
    assert m.tipo == "uscita" and m.stato == "proposto" and m.categoria_id is None
    # è nella coda "da smistare"
    assert [x.id for x in fat.list_non_collegate(tipo="passiva")] == [f.id]

    # idempotente su sdi_id
    s2 = sincronizza_passive(client=client, fattura_repo=fat, movimento_repo=mov,
                             piva_azienda=PIVA_LYS)
    assert s2.duplicate == 1 and s2.nuove == 0


def test_nota_credito_passiva_inverte_segno(db: Path):
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    client = FakeSdiClient(inbox=[
        FatturaPassivaRaw(
            sdi_id="SDI-NC",
            xml_bytes=_xml(ced_piva=PIVA_FORNITORE, cess_piva=PIVA_LYS,
                           numero="NC-1", tipo_doc="TD04"),
            filename="NC-1.xml",
        ),
    ])
    sincronizza_passive(client=client, fattura_repo=fat, movimento_repo=mov,
                        piva_azienda=PIVA_LYS)
    f = fat.list(tipo="passiva")[0]
    m = mov.list_by_fattura(f.id)[0]
    assert m.tipo == "entrata"  # nota di credito passiva → rientro di costo
