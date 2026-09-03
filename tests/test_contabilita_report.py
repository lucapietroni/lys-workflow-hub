"""Test dashboard costi/ricavi per categoria/periodo (Fase 4)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lys_workflow_hub.core.contabilita_categoria_repository import (
    ContabilitaCategoriaRepository,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.workflows.contabilita.report import costruisci_report


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "app.db"
    cat = ContabilitaCategoriaRepository(db_path=p)
    mov = ContabilitaMovimentoRepository(db_path=p)
    ric = next(c for c in cat.list_all() if c.nome == "Riparazioni carrozzeria")
    ricambi = next(c for c in cat.list_all() if c.nome == "Ricambi")
    manod = next(c for c in cat.list_all() if c.nome == "Manodopera")

    mov.create(data="2026-01-15", importo="3000", tipo="entrata", categoria_id=ric.id)
    mov.create(data="2026-02-10", importo="800", tipo="uscita", categoria_id=ricambi.id)
    mov.create(data="2026-02-20", importo="500", tipo="uscita", categoria_id=manod.id)
    mov.create(data="2026-03-05", importo="120", tipo="uscita")  # senza categoria
    # proposto → escluso
    mov.create(
        data="2026-02-15", importo="9999", tipo="uscita", categoria_id=ricambi.id,
        origine="da_fattura_sdi", stato="proposto",
    )
    return p


def test_report_aggrega_per_categoria(db: Path):
    r = costruisci_report(db)
    assert r.entrate_tot == 3000.0
    assert r.uscite_tot == 1420.0  # 800 + 500 + 120, proposto escluso
    assert r.margine == 1580.0
    per_nome = {x.nome: x for x in r.righe}
    assert per_nome["Ricambi"].uscite == 800.0
    assert per_nome["(senza categoria)"].uscite == 120.0


def test_report_filtro_periodo(db: Path):
    r = costruisci_report(db, dal="2026-02-01", al="2026-02-28")
    assert r.entrate_tot == 0.0
    assert r.uscite_tot == 1300.0  # solo febbraio (800 + 500)


def test_report_ricavi_e_costi_separati(db: Path):
    r = costruisci_report(db)
    assert all(x.entrate > 0 or x.tipo == "ricavo" for x in r.ricavi)
    assert all(x.uscite > 0 or x.tipo == "costo" for x in r.costi)
    nomi_costi = {x.nome for x in r.costi}
    assert "Ricambi" in nomi_costi and "Riparazioni carrozzeria" not in nomi_costi
