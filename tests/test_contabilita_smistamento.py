"""Test smistamento fatture passive (Fase 4)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lys_workflow_hub.core.contabilita_categoria_repository import (
    ContabilitaCategoriaRepository,
)
from lys_workflow_hub.core.contabilita_fattura_repository import (
    ContabilitaFatturaRepository,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.workflows.contabilita.smistamento import (
    Assegnazione,
    SmistamentoError,
    coda_passive,
    smista_fattura,
)


@pytest.fixture
def setup(tmp_path: Path):
    db = tmp_path / "app.db"
    cat = ContabilitaCategoriaRepository(db_path=db)
    fat = ContabilitaFatturaRepository(db_path=db)
    mov = ContabilitaMovimentoRepository(db_path=db)
    # fattura passiva con un movimento proposto (come la lascia la Fase 3)
    f = fat.create(
        tipo="passiva", numero="F-1", anno=2026, data="2026-04-01",
        controparte_nome="Fornitore X", controparte_piva="01234567890",
        imponibile="1000", importo_iva="220", importo_totale="1220",
    )
    mov.create(
        data="2026-04-01", importo="1220", tipo="uscita", fattura_id=f.id,
        origine="da_fattura_sdi", stato="proposto",
    )
    ricambi = next(c for c in cat.list_all() if c.nome == "Ricambi")
    return db, cat, fat, mov, f, ricambi


def test_coda_contiene_la_fattura_proposta(setup):
    _db, _cat, fat, mov, f, _ric = setup
    voci = coda_passive(fat, mov)
    assert [v.fattura.id for v in voci] == [f.id]
    assert voci[0].movimento.stato == "proposto"


def test_smista_singola_pratica_intero_importo(setup):
    _db, _cat, fat, mov, f, ric = setup
    creati = smista_fattura(
        fattura_repo=fat, movimento_repo=mov, fattura_id=f.id,
        categoria_id=ric.id,
        assegnazioni=[Assegnazione(pratica_id=766, importo=1220.0)],
    )
    assert len(creati) == 1
    m = creati[0]
    assert m.pratica_id == 766 and m.categoria_id == ric.id
    assert m.stato == "confermato" and m.importo == 1220.0
    # niente più proposti → esce dalla coda
    assert coda_passive(fat, mov) == []
    # riga ponte creata
    assert [r.pratica_id for r in fat.list_pratiche(f.id)] == [766]


def test_smista_split_con_residuo(setup):
    _db, _cat, fat, mov, f, ric = setup
    creati = smista_fattura(
        fattura_repo=fat, movimento_repo=mov, fattura_id=f.id,
        categoria_id=ric.id,
        assegnazioni=[
            Assegnazione(pratica_id=10, importo=700.0),
            Assegnazione(pratica_id=20, importo=300.0),
        ],
    )
    importi = sorted(m.importo for m in creati)
    assert importi == [220.0, 300.0, 700.0]  # 220 = residuo senza pratica
    senza_pratica = [m for m in creati if m.pratica_id is None]
    assert len(senza_pratica) == 1 and senza_pratica[0].importo == 220.0
    assert {r.pratica_id for r in fat.list_pratiche(f.id)} == {10, 20}


def test_smista_spesa_generale_senza_pratiche(setup):
    _db, cat, fat, mov, f, _ric = setup
    affitto = next(c for c in cat.list_all() if c.nome == "Affitto")
    creati = smista_fattura(
        fattura_repo=fat, movimento_repo=mov, fattura_id=f.id,
        categoria_id=affitto.id, assegnazioni=[],
    )
    assert len(creati) == 1
    assert creati[0].pratica_id is None
    assert creati[0].importo == 1220.0
    assert creati[0].categoria_id == affitto.id
    assert coda_passive(fat, mov) == []


def test_smista_rifiuta_somma_eccedente(setup):
    _db, _cat, fat, mov, f, ric = setup
    with pytest.raises(SmistamentoError, match="supera"):
        smista_fattura(
            fattura_repo=fat, movimento_repo=mov, fattura_id=f.id,
            categoria_id=ric.id,
            assegnazioni=[Assegnazione(pratica_id=1, importo=2000.0)],
        )
    # nessuna modifica: il movimento proposto è ancora lì
    assert coda_passive(fat, mov)


def test_smista_idempotente_su_ripetizione(setup):
    _db, _cat, fat, mov, f, ric = setup
    smista_fattura(
        fattura_repo=fat, movimento_repo=mov, fattura_id=f.id,
        categoria_id=ric.id,
        assegnazioni=[Assegnazione(pratica_id=766, importo=1220.0)],
    )
    # ri-smisto con pratica diversa: sostituisce, non accumula
    smista_fattura(
        fattura_repo=fat, movimento_repo=mov, fattura_id=f.id,
        categoria_id=ric.id,
        assegnazioni=[Assegnazione(pratica_id=999, importo=1220.0)],
    )
    movimenti = mov.list_by_fattura(f.id)
    assert len(movimenti) == 1
    assert movimenti[0].pratica_id == 999
    assert {r.pratica_id for r in fat.list_pratiche(f.id)} == {999}


def test_smista_non_tocca_movimenti_gia_confermati(setup):
    _db, _cat, fat, mov, f, ric = setup
    # un movimento manuale confermato sulla stessa fattura non va perso
    manuale = mov.create(
        data="2026-04-02", importo="50", tipo="uscita", fattura_id=f.id,
        descrizione="spesa accessoria", stato="confermato",
    )
    smista_fattura(
        fattura_repo=fat, movimento_repo=mov, fattura_id=f.id,
        categoria_id=ric.id,
        assegnazioni=[Assegnazione(pratica_id=766, importo=1220.0)],
    )
    ids = {m.id for m in mov.list_by_fattura(f.id)}
    assert manuale.id in ids
