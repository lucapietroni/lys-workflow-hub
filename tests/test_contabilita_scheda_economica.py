"""Test della scheda economica pratica (Fase 2) — funzione aggregata pura."""
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
from lys_workflow_hub.workflows.contabilita.scheda_economica import (
    costruisci_scheda_economica,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "app.db"
    ContabilitaCategoriaRepository(db_path=p)  # crea schema + seed
    ContabilitaFatturaRepository(db_path=p)
    ContabilitaMovimentoRepository(db_path=p)
    return p


def test_scheda_vuota(db: Path):
    se = costruisci_scheda_economica(db, 766)
    assert se.pratica_numero == 766
    assert se.entrate_tot == 0.0
    assert se.uscite_tot == 0.0
    assert se.margine == 0.0
    assert se.ha_dati is False


def test_scheda_margine_solo_confermati(db: Path):
    mov = ContabilitaMovimentoRepository(db_path=db)
    cat = ContabilitaCategoriaRepository(db_path=db)
    ricambi = next(c for c in cat.list_all() if c.nome == "Ricambi")

    mov.create(data="2026-05-01", importo="2000", tipo="entrata", pratica_id=766)
    mov.create(
        data="2026-05-02", importo="450", tipo="uscita",
        pratica_id=766, categoria_id=ricambi.id,
    )
    # proposto → escluso dal margine, contato a parte
    mov.create(
        data="2026-05-03", importo="99", tipo="uscita", pratica_id=766,
        stato="proposto", origine="da_fattura_sdi",
    )
    # altra pratica → ignorato
    mov.create(data="2026-05-04", importo="5000", tipo="uscita", pratica_id=1)

    se = costruisci_scheda_economica(db, 766)
    assert se.entrate_tot == 2000.0
    assert se.uscite_tot == 450.0
    assert se.margine == 1550.0
    assert se.movimenti_proposti_n == 1
    assert len(se.movimenti) == 2  # solo confermati
    assert se.ha_dati is True


def test_scheda_ripartizione_per_categoria(db: Path):
    mov = ContabilitaMovimentoRepository(db_path=db)
    cat = ContabilitaCategoriaRepository(db_path=db)
    ricambi = next(c for c in cat.list_all() if c.nome == "Ricambi")
    manod = next(c for c in cat.list_all() if c.nome == "Manodopera")

    mov.create(data="2026-05-01", importo="300", tipo="uscita", pratica_id=766, categoria_id=ricambi.id)
    mov.create(data="2026-05-02", importo="200", tipo="uscita", pratica_id=766, categoria_id=ricambi.id)
    mov.create(data="2026-05-03", importo="150", tipo="uscita", pratica_id=766, categoria_id=manod.id)
    mov.create(data="2026-05-04", importo="80", tipo="uscita", pratica_id=766)  # senza categoria

    se = costruisci_scheda_economica(db, 766)
    per_nome = {r.nome: r.totale for r in se.per_categoria}
    assert per_nome["Ricambi"] == -500.0
    assert per_nome["Manodopera"] == -150.0
    assert per_nome["(senza categoria)"] == -80.0


def test_scheda_include_fatture_collegate_ma_non_nel_margine(db: Path):
    mov = ContabilitaMovimentoRepository(db_path=db)
    fat = ContabilitaFatturaRepository(db_path=db)

    mov.create(data="2026-05-01", importo="1000", tipo="entrata", pratica_id=766)
    f = fat.create(
        tipo="passiva", numero="F-9", anno=2026, data="2026-05-01",
        controparte_nome="Fornitore X", controparte_piva="01234567890",
        importo_totale="1220",
    )
    fat.link_pratica(f.id, 766, importo_assegnato="1220")

    se = costruisci_scheda_economica(db, 766)
    assert len(se.fatture) == 1
    assert se.fatture[0].importo_assegnato == 1220.0
    # La fattura NON entra nel margine (solo i movimenti lo fanno).
    assert se.margine == 1000.0
