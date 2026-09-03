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
from lys_workflow_hub.core.contabilita_categoria_repository import (
    ContabilitaCategoriaRepository,
)
from lys_workflow_hub.core.wincar_fatture_repository import numero_fattura_int
from lys_workflow_hub.workflows.contabilita.sdi_import import (
    STATO_SDI_DA_INVIARE,
    STATO_SDI_INVIATA,
    classifica_tipo,
    collega_attive_da_wincar,
    importa_attive_da_dir,
    invia_attive_pendenti,
    parse_fattura_xml,
    sincronizza_passive,
)

PIVA_LYS = "14521721002"
PIVA_CLIENTE = "09876543210"
PIVA_FORNITORE = "01112223330"


class FakeWinCarFatture:
    """Sostituto di WinCarFattureRepository per i test."""

    def __init__(self, mapping: dict[tuple[int, int], int] | None = None):
        self.mapping = mapping or {}

    def disponibile(self) -> bool:
        return True

    def pratica_per_fattura(self, numero: int, anno: int, *, alfa: str = "") -> int | None:
        return self.mapping.get((int(numero), int(anno)))


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


def test_parse_rifiuta_xml_con_entita_o_doctype():
    billion_laughs = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        b"<p:FatturaElettronica xmlns:p=\"x\">&lol;</p:FatturaElettronica>"
    )
    with pytest.raises(ValueError, match="DTD|entità"):
        parse_fattura_xml(billion_laughs)


def test_parse_multi_body_usa_il_primo():
    body = (
        "<FatturaElettronicaBody><DatiGenerali><DatiGeneraliDocumento>"
        "<TipoDocumento>TD01</TipoDocumento><Data>2026-05-10</Data>"
        "<Numero>{n}</Numero><ImportoTotaleDocumento>10.00</ImportoTotaleDocumento>"
        "</DatiGeneraliDocumento></DatiGenerali><DatiBeniServizi><DatiRiepilogo>"
        "<ImponibileImporto>10.00</ImponibileImporto><Imposta>0.00</Imposta>"
        "</DatiRiepilogo></DatiBeniServizi></FatturaElettronicaBody>"
    )
    xml = (
        '<?xml version="1.0"?><p:FatturaElettronica '
        'xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2">'
        "<FatturaElettronicaHeader><CedentePrestatore><DatiAnagrafici>"
        f"<IdFiscaleIVA><IdCodice>{PIVA_LYS}</IdCodice></IdFiscaleIVA>"
        "<Anagrafica><Denominazione>LYS</Denominazione></Anagrafica>"
        "</DatiAnagrafici></CedentePrestatore><CessionarioCommittente><DatiAnagrafici>"
        f"<IdFiscaleIVA><IdCodice>{PIVA_CLIENTE}</IdCodice></IdFiscaleIVA>"
        "</DatiAnagrafici></CessionarioCommittente></FatturaElettronicaHeader>"
        + body.format(n="1") + body.format(n="2")
        + "</p:FatturaElettronica>"
    ).encode()
    fx = parse_fattura_xml(xml)
    assert fx.numero == "1"


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


def test_importa_attive_default_storico_idempotente(db: Path, tmp_path: Path):
    src = tmp_path / "wincar_attive"
    src.mkdir()
    (src / "IT_00001.xml").write_bytes(_xml(numero="1", data="2026-03-01"))
    (src / "IT_00002.xml").write_bytes(_xml(numero="2", data="2026-04-01"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)

    s1 = importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat, movimento_repo=mov, anno=2026,
    )
    assert (s1.esaminati, s1.nuove, s1.duplicate) == (2, 2, 0)
    fatture = fat.list(tipo="attiva")
    assert all(f.stato_sdi == "storico" for f in fatture)  # NON re-inviate
    # senza categoria → movimenti proposto
    for f in fatture:
        m = mov.list_by_fattura(f.id)[0]
        assert m.tipo == "entrata" and m.stato == "proposto"

    s2 = importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat, movimento_repo=mov, anno=2026,
    )
    assert (s2.nuove, s2.duplicate) == (0, 2)


def test_importa_attive_filtro_anno_e_cutoff(db: Path, tmp_path: Path):
    from datetime import date

    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="2025", data="2025-11-30"))
    (src / "b.xml").write_bytes(_xml(numero="2026a", data="2026-02-10"))
    (src / "c.xml").write_bytes(_xml(numero="2026b", data="2026-07-20"))
    fat = ContabilitaFatturaRepository(db_path=db)

    s = importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat,
        anno=2026, since=date(2026, 1, 1),
    )
    assert s.nuove == 2
    assert s.fuori_periodo == 1  # la 2025
    assert {f.numero for f in fat.list(tipo="attiva")} == {"2026a", "2026b"}


def test_importa_attive_con_categoria_crea_movimento_confermato(db: Path, tmp_path: Path):
    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="9", data="2026-05-01", totale="1220.00"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    from lys_workflow_hub.core.contabilita_categoria_repository import (
        ContabilitaCategoriaRepository,
    )
    cat = ContabilitaCategoriaRepository(db_path=db)
    ric = next(c for c in cat.list_all() if c.nome == "Riparazioni carrozzeria")

    importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat, movimento_repo=mov,
        anno=2026, categoria_id=ric.id,
    )
    m = mov.list_by_fattura(fat.list(tipo="attiva")[0].id)[0]
    assert m.stato == "confermato" and m.categoria_id == ric.id and m.tipo == "entrata"


def test_marca_da_inviare_poi_invio(db: Path, tmp_path: Path):
    from lys_workflow_hub.workflows.contabilita.sdi_import import marca_da_inviare

    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="77", data="2026-05-01", totale="1220.00"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat, movimento_repo=mov, anno=2026,
    )
    fid = fat.list(tipo="attiva")[0].id

    # storico → non viene inviata
    assert invia_attive_pendenti(client=FakeSdiClient(), fattura_repo=fat,
                                 movimento_repo=mov).inviate == 0

    marca_da_inviare(fat, fid)
    assert fat.get(fid).stato_sdi == STATO_SDI_DA_INVIARE
    s = invia_attive_pendenti(client=FakeSdiClient(), fattura_repo=fat, movimento_repo=mov)
    assert s.inviate == 1
    assert fat.get(fid).stato_sdi == STATO_SDI_INVIATA


def test_importa_attive_dir_mancante(db: Path, tmp_path: Path):
    s = importa_attive_da_dir(tmp_path / "nope", piva_azienda=PIVA_LYS,
                              fattura_repo=ContabilitaFatturaRepository(db_path=db))
    assert s.esaminati == 0
    assert s.errori


def test_numero_fattura_int():
    assert numero_fattura_int("40") == 40
    assert numero_fattura_int("2026/40") == 40
    assert numero_fattura_int("40/A") == 40
    assert numero_fattura_int("") is None
    assert numero_fattura_int(None) is None


def test_importa_attive_auto_collega_pratica_da_wincar(db: Path, tmp_path: Path):
    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="40", data="2026-05-28", totale="854.00",
                                     imponibile="700.00", imposta="154.00"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    cat = ContabilitaCategoriaRepository(db_path=db)
    ric = next(c for c in cat.list_all() if c.nome == "Riparazioni carrozzeria")
    wincar = FakeWinCarFatture({(40, 2026): 827})

    s = importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat, movimento_repo=mov,
        wincar_fatture_repo=wincar, anno=2026, categoria_id=ric.id,
    )
    assert s.nuove == 1 and s.collegate_pratica == 1
    f = fat.list(tipo="attiva")[0]
    assert [r.pratica_id for r in fat.list_pratiche(f.id)] == [827]
    m = mov.list_by_fattura(f.id)[0]
    assert m.pratica_id == 827 and m.categoria_id == ric.id
    assert m.stato == "confermato" and m.tipo == "entrata"


def test_importa_attive_pratica_non_in_wincar_resta_da_smistare(db: Path, tmp_path: Path):
    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="99", data="2026-05-01"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    cat = ContabilitaCategoriaRepository(db_path=db)
    ric = next(c for c in cat.list_all() if c.nome == "Riparazioni carrozzeria")

    s = importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat, movimento_repo=mov,
        wincar_fatture_repo=FakeWinCarFatture({}), anno=2026, categoria_id=ric.id,
    )
    assert s.collegate_pratica == 0
    f = fat.list(tipo="attiva")[0]
    assert fat.list_pratiche(f.id) == []
    # categoria c'è → confermato anche senza pratica
    assert mov.list_by_fattura(f.id)[0].stato == "confermato"


def test_collega_attive_da_wincar_one_shot(db: Path, tmp_path: Path):
    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="40", data="2026-05-28", totale="854.00",
                                     imponibile="700.00", imposta="154.00"))
    (src / "b.xml").write_bytes(_xml(numero="41", data="2026-05-29"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    cat = ContabilitaCategoriaRepository(db_path=db)
    ric = next(c for c in cat.list_all() if c.nome == "Riparazioni carrozzeria")

    # import SENZA wincar → tutte da smistare (proposto, no pratica)
    importa_attive_da_dir(src, piva_azienda=PIVA_LYS, fattura_repo=fat,
                          movimento_repo=mov, anno=2026)
    assert all(not fat.list_pratiche(f.id) for f in fat.list(tipo="attiva"))

    # one-shot: collega dalla mappa WinCar
    s = collega_attive_da_wincar(
        fattura_repo=fat, movimento_repo=mov,
        wincar_fatture_repo=FakeWinCarFatture({(40, 2026): 827}),
        categoria_id=ric.id,
    )
    assert s.collegate == 1 and s.categorizzate == 1
    per_numero = {f.numero: f for f in fat.list(tipo="attiva")}
    assert [r.pratica_id for r in fat.list_pratiche(per_numero["40"].id)] == [827]
    m40 = mov.list_by_fattura(per_numero["40"].id)[0]
    assert m40.pratica_id == 827 and m40.categoria_id == ric.id and m40.stato == "confermato"
    # la 41: nessuna pratica in WinCar → solo categoria, confermato
    assert fat.list_pratiche(per_numero["41"].id) == []
    m41 = mov.list_by_fattura(per_numero["41"].id)[0]
    assert m41.categoria_id == ric.id and m41.stato == "confermato"

    # IVA propagata sui movimenti (fattura 40: totale 854, IVA 154)
    assert m40.importo_iva == 154.0

    # idempotente: seconda passata non tocca nulla
    s2 = collega_attive_da_wincar(
        fattura_repo=fat, movimento_repo=mov,
        wincar_fatture_repo=FakeWinCarFatture({(40, 2026): 827}),
        categoria_id=ric.id,
    )
    assert s2.collegate == 0 and s2.categorizzate == 0 and s2.gia_sistemate == 2


def test_collega_ripristina_iva_su_movimenti_gia_smistati(db: Path, tmp_path: Path):
    """Movimenti smistati da una versione precedente senza IVA vengono
    ri-sistemati (aggiunta IVA)."""
    from lys_workflow_hub.core.contabilita_categoria_repository import (
        ContabilitaCategoriaRepository,
    )

    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="40", data="2026-05-28", totale="854.00",
                                     imponibile="700.00", imposta="154.00"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    ric = next(c for c in ContabilitaCategoriaRepository(db_path=db).list_all()
               if c.nome == "Riparazioni carrozzeria")
    importa_attive_da_dir(src, piva_azienda=PIVA_LYS, fattura_repo=fat,
                          movimento_repo=mov, anno=2026)
    fid = fat.list(tipo="attiva")[0].id
    # simulа un movimento smistato "vecchio": confermato, categoria, pratica, IVA None
    mov.delete_by_fattura(fid, solo_sdi=True)
    fat.link_pratica(fid, 827, importo_assegnato=854.0)
    mov.create(data="2026-05-28", importo="854", tipo="entrata", categoria_id=ric.id,
               pratica_id=827, fattura_id=fid, origine="da_fattura_sdi",
               stato="confermato")  # niente importo_iva

    s = collega_attive_da_wincar(
        fattura_repo=fat, movimento_repo=mov,
        wincar_fatture_repo=FakeWinCarFatture({(40, 2026): 827}),
        categoria_id=ric.id,
    )
    assert s.collegate == 1
    m = mov.list_by_fattura(fid)[0]
    assert m.importo_iva == 154.0 and m.pratica_id == 827


def test_nota_credito_attiva_usa_categoria_nota_di_credito(db: Path, tmp_path: Path):
    src = tmp_path / "attive"
    src.mkdir()
    (src / "nc.xml").write_bytes(_xml(numero="58", data="2026-07-17", tipo_doc="TD04",
                                     totale="20000.00", imponibile="16393.44",
                                     imposta="3606.56"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    cat = ContabilitaCategoriaRepository(db_path=db)
    ric = next(c for c in cat.list_all() if c.nome == "Riparazioni carrozzeria")
    nc = next(c for c in cat.list_all() if c.nome == "Nota di credito")

    importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat, movimento_repo=mov,
        wincar_fatture_repo=FakeWinCarFatture({}), anno=2026,
        categoria_id=ric.id, categoria_nc_id=nc.id,
    )
    m = mov.list_by_fattura(fat.list(tipo="attiva")[0].id)[0]
    assert m.categoria_id == nc.id
    assert m.tipo == "uscita"  # NC attiva = storno di ricavo
    assert m.stato == "confermato"


# --------------------------------------------------------------------------- invio


def test_invia_attive_pendenti_crea_movimento_proposto(db: Path, tmp_path: Path):
    src = tmp_path / "attive"
    src.mkdir()
    (src / "a.xml").write_bytes(_xml(numero="55", data="2026-05-01", totale="1220.00"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    importa_attive_da_dir(
        src, piva_azienda=PIVA_LYS, fattura_repo=fat, anno=2026, come_storico=False,
    )

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
    (src / "a.xml").write_bytes(_xml(numero="1", data="2026-05-01"))
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    importa_attive_da_dir(src, piva_azienda=PIVA_LYS, fattura_repo=fat,
                          anno=2026, come_storico=False)
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
