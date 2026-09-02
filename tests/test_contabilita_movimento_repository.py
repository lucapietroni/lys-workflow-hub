"""Test del repository SQLite per i movimenti di contabilità gestionale."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lys_workflow_hub.core.contabilita_movimento_repository import (
    ContabilitaMovimentoRepository,
)


@pytest.fixture
def repo(tmp_path: Path) -> ContabilitaMovimentoRepository:
    return ContabilitaMovimentoRepository(db_path=tmp_path / "test.db")


def test_create_movimento_minimo(repo: ContabilitaMovimentoRepository):
    m = repo.create(data="2026-03-01", importo="1500,50", tipo="uscita")
    assert m.id is not None
    assert m.data == date(2026, 3, 1)
    assert m.importo == 1500.5
    assert m.tipo == "uscita"
    assert m.stato == "confermato"
    assert m.origine == "manuale"
    assert m.pratica_id is None
    assert m.categoria_id is None


def test_create_con_pratica_e_iva(repo: ContabilitaMovimentoRepository):
    m = repo.create(
        data="2026-03-02",
        importo="1000",
        tipo="entrata",
        pratica_id=766,
        importo_iva="220",
        descrizione="Acconto riparazione",
    )
    assert m.pratica_id == 766
    assert m.importo_iva == 220.0
    assert m.importo_con_segno == 1000.0


def test_create_rifiuta_valori_non_validi(repo: ContabilitaMovimentoRepository):
    with pytest.raises(ValueError, match="[Dd]ata"):
        repo.create(data="01/03/2026", importo="10", tipo="uscita")
    with pytest.raises(ValueError, match="importo"):
        repo.create(data="2026-03-01", importo="", tipo="uscita")
    with pytest.raises(ValueError, match="negativo"):
        repo.create(data="2026-03-01", importo="-5", tipo="uscita")
    with pytest.raises(ValueError, match="[Tt]ipo"):
        repo.create(data="2026-03-01", importo="5", tipo="giroconto")
    with pytest.raises(ValueError, match="[Ss]tato"):
        repo.create(data="2026-03-01", importo="5", tipo="uscita", stato="bozza")


def test_list_filtri(repo: ContabilitaMovimentoRepository):
    repo.create(data="2026-01-10", importo="100", tipo="uscita", categoria_id=1, pratica_id=10)
    repo.create(data="2026-02-10", importo="200", tipo="entrata", categoria_id=2, pratica_id=10)
    repo.create(data="2026-03-10", importo="300", tipo="uscita", categoria_id=1, pratica_id=99)

    assert len(repo.list()) == 3
    assert len(repo.list(pratica_id=10)) == 2
    assert len(repo.list(categoria_id=1)) == 2
    assert len(repo.list(tipo="entrata")) == 1
    assert len(repo.list(dal="2026-02-01", al="2026-02-28")) == 1
    # Ordine: data desc
    assert repo.list()[0].data == date(2026, 3, 10)


def test_list_data_malformata_solleva_value_error(repo: ContabilitaMovimentoRepository):
    with pytest.raises(ValueError):
        repo.list(dal="marzo")


def test_totali(repo: ContabilitaMovimentoRepository):
    repo.create(data="2026-01-10", importo="100", tipo="uscita")
    repo.create(data="2026-01-11", importo="250", tipo="uscita")
    repo.create(data="2026-01-12", importo="900", tipo="entrata")
    t = repo.totali()
    assert t.entrate == 900.0
    assert t.uscite == 350.0
    assert t.saldo == 550.0


def test_totali_esclude_per_stato(repo: ContabilitaMovimentoRepository):
    repo.create(data="2026-01-10", importo="100", tipo="uscita", stato="confermato")
    repo.create(
        data="2026-01-11", importo="500", tipo="uscita",
        stato="proposto", origine="da_fattura_sdi",
    )
    assert repo.totali(stato="confermato").uscite == 100.0


def test_update_movimento(repo: ContabilitaMovimentoRepository):
    m = repo.create(data="2026-01-10", importo="100", tipo="uscita")
    up = repo.update(
        m.id, data="2026-01-15", importo="120", tipo="uscita",
        categoria_id=3, descrizione="rettifica",
    )
    assert up.importo == 120.0
    assert up.data == date(2026, 1, 15)
    assert up.categoria_id == 3
    assert up.descrizione == "rettifica"


def test_set_stato_e_delete(repo: ContabilitaMovimentoRepository):
    m = repo.create(
        data="2026-01-10", importo="100", tipo="uscita",
        stato="proposto", origine="da_fattura_sdi",
    )
    repo.set_stato(m.id, "confermato")
    assert repo.get(m.id).stato == "confermato"
    assert repo.delete(m.id) is True
    assert repo.get(m.id) is None
