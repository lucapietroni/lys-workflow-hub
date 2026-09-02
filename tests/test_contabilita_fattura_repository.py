"""Test del repository SQLite per fatture + tabella ponte fattura↔pratica."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lys_workflow_hub.core.contabilita_fattura_repository import (
    ContabilitaFatturaRepository,
)


@pytest.fixture
def repo(tmp_path: Path) -> ContabilitaFatturaRepository:
    return ContabilitaFatturaRepository(db_path=tmp_path / "test.db")


def _crea_passiva(repo, **kw):
    base = dict(
        tipo="passiva",
        numero="F-123",
        anno=2026,
        data="2026-04-01",
        controparte_nome="Fornitore Ricambi SRL",
        controparte_piva="01234567890",
        imponibile="1000",
        importo_iva="220",
        importo_totale="1220",
    )
    base.update(kw)
    return repo.create(**base)


def test_create_fattura(repo: ContabilitaFatturaRepository):
    f = _crea_passiva(repo)
    assert f.id is not None
    assert f.tipo == "passiva"
    assert f.data == date(2026, 4, 1)
    assert f.importo_totale == 1220.0
    assert f.origine == "manuale"


def test_create_idempotente_su_chiave_naturale(repo: ContabilitaFatturaRepository):
    f1 = _crea_passiva(repo)
    f2 = _crea_passiva(repo, importo_totale="9999")  # stessa (tipo, numero, anno, piva)
    assert f1.id == f2.id
    assert repo.get(f1.id).importo_totale == 1220.0  # non modificata


def test_create_idempotente_su_sdi_id(repo: ContabilitaFatturaRepository):
    f1 = _crea_passiva(repo, sdi_id="SDI-999")
    f2 = _crea_passiva(repo, numero="DIVERSO", sdi_id="SDI-999")
    assert f1.id == f2.id


def test_create_rifiuta_tipo_o_numero_non_validi(repo: ContabilitaFatturaRepository):
    with pytest.raises(ValueError, match="[Tt]ipo"):
        _crea_passiva(repo, tipo="nota_credito")
    with pytest.raises(ValueError, match="numero"):
        _crea_passiva(repo, numero="   ")


def test_link_e_split_su_piu_pratiche(repo: ContabilitaFatturaRepository):
    f = _crea_passiva(repo)
    repo.link_pratica(f.id, 100, importo_assegnato="700")
    repo.link_pratica(f.id, 200, importo_assegnato="520")
    righe = repo.list_pratiche(f.id)
    assert {r.pratica_id for r in righe} == {100, 200}
    assert sum(r.importo_assegnato for r in righe) == 1220.0


def test_link_upsert_non_duplica(repo: ContabilitaFatturaRepository):
    f = _crea_passiva(repo)
    repo.link_pratica(f.id, 100, importo_assegnato="700")
    repo.link_pratica(f.id, 100, importo_assegnato="1220")
    righe = repo.list_pratiche(f.id)
    assert len(righe) == 1
    assert righe[0].importo_assegnato == 1220.0


def test_unlink_pratica(repo: ContabilitaFatturaRepository):
    f = _crea_passiva(repo)
    repo.link_pratica(f.id, 100)
    assert repo.unlink_pratica(f.id, 100) is True
    assert repo.list_pratiche(f.id) == []


def test_list_non_collegate(repo: ContabilitaFatturaRepository):
    f1 = _crea_passiva(repo, numero="A-1")
    f2 = _crea_passiva(repo, numero="A-2")
    repo.link_pratica(f2.id, 100)
    non_coll = repo.list_non_collegate(tipo="passiva")
    assert [f.id for f in non_coll] == [f1.id]


def test_list_fatture_per_pratica(repo: ContabilitaFatturaRepository):
    f = _crea_passiva(repo)
    repo.link_pratica(f.id, 100, importo_assegnato="500")
    coppie = repo.list_fatture_per_pratica(100)
    assert len(coppie) == 1
    fattura, assegnato = coppie[0]
    assert fattura.id == f.id
    assert assegnato == 500.0


def test_delete_cascata_ponte(repo: ContabilitaFatturaRepository):
    f = _crea_passiva(repo)
    repo.link_pratica(f.id, 100)
    assert repo.delete(f.id) is True
    assert repo.get(f.id) is None
    assert repo.list_pratiche(f.id) == []
