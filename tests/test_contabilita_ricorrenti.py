"""Test costi ricorrenti non fatturati (Fase 5)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lys_workflow_hub.core.contabilita_categoria_repository import (
    ContabilitaCategoriaRepository,
)
from lys_workflow_hub.core.contabilita_costo_ricorrente_repository import (
    ContabilitaCostoRicorrenteRepository,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ContabilitaMovimentoRepository,
)
from lys_workflow_hub.workflows.contabilita.ricorrenti import (
    genera_movimenti_ricorrenti,
    periodi_da_generare,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "app.db"
    ContabilitaCategoriaRepository(db_path=p)
    ContabilitaCostoRicorrenteRepository(db_path=p)
    ContabilitaMovimentoRepository(db_path=p)
    return p


def _crea(db: Path, **kw):
    r = ContabilitaCostoRicorrenteRepository(db_path=db)
    base = dict(
        nome="Affitto", categoria_id=None, importo="1200", cadenza="mensile",
        giorno_mese="1", data_inizio="2026-01-01",
    )
    base.update(kw)
    return r.create(**base)


# ------------------------------------------------------------------- repo CRUD


def test_create_e_validazione(db: Path):
    c = _crea(db, importo="1234,56", importo_iva="271,60")
    assert c.importo == 1234.56 and c.importo_iva == 271.6
    assert c.data_inizio == date(2026, 1, 1) and c.attivo is True
    with pytest.raises(ValueError, match="[Nn]ome"):
        _crea(db, nome="")
    with pytest.raises(ValueError, match="[Cc]adenza"):
        _crea(db, cadenza="settimanale")
    with pytest.raises(ValueError, match="negativo"):
        _crea(db, importo="-5")


def test_giorno_mese_clamp(db: Path):
    assert _crea(db, giorno_mese="31").giorno_mese == 28
    assert _crea(db, nome="X", giorno_mese="0").giorno_mese == 1


# ----------------------------------------------------------------- periodi


def test_periodi_da_generare_mensile_backfill(db: Path):
    c = _crea(db, data_inizio="2026-03-01")
    per = periodi_da_generare(c, date(2026, 6, 15))
    assert per == [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]


def test_periodi_trimestrale(db: Path):
    c = _crea(db, cadenza="trimestrale", data_inizio="2026-01-15", giorno_mese="15")
    per = periodi_da_generare(c, date(2026, 12, 31))
    assert per == [date(2026, 1, 15), date(2026, 4, 15), date(2026, 7, 15), date(2026, 10, 15)]


def test_periodi_rispetta_watermark(db: Path):
    c = _crea(db, data_inizio="2026-01-01")
    repo = ContabilitaCostoRicorrenteRepository(db_path=db)
    repo.segna_periodo_generato(c.id, date(2026, 4, 1))
    c2 = repo.get(c.id)
    per = periodi_da_generare(c2, date(2026, 6, 1))
    assert per == [date(2026, 5, 1), date(2026, 6, 1)]


# ------------------------------------------------------------- generazione


def test_genera_crea_movimenti_e_idempotente(db: Path):
    cat = ContabilitaCategoriaRepository(db_path=db)
    affitto_cat = next(c for c in cat.list_all() if c.nome == "Affitto")
    _crea(db, categoria_id=affitto_cat.id, importo="1200", importo_iva="264",
          data_inizio="2026-01-01")

    s1 = genera_movimenti_ricorrenti(db, oggi=date(2026, 4, 10))
    assert s1.movimenti_creati == 4  # gen, feb, mar, apr
    mov = ContabilitaMovimentoRepository(db_path=db)
    movimenti = mov.list()
    assert len(movimenti) == 4
    m = movimenti[0]
    assert m.tipo == "uscita" and m.importo == 1200.0 and m.importo_iva == 264.0
    assert m.categoria_id == affitto_cat.id and m.stato == "confermato"
    assert m.origine == "ricorrente"
    assert "01/2026" in movimenti[-1].descrizione

    # 2a passata stesso giorno → niente
    s2 = genera_movimenti_ricorrenti(db, oggi=date(2026, 4, 10))
    assert s2.movimenti_creati == 0

    # mese dopo → 1 nuovo
    s3 = genera_movimenti_ricorrenti(db, oggi=date(2026, 5, 2))
    assert s3.movimenti_creati == 1
    assert len(mov.list()) == 5


def test_movimento_eliminato_non_viene_ricreato(db: Path):
    _crea(db, data_inizio="2026-01-01")
    genera_movimenti_ricorrenti(db, oggi=date(2026, 2, 15))
    mov = ContabilitaMovimentoRepository(db_path=db)
    assert len(mov.list()) == 2
    mov.delete(mov.list()[0].id)  # elimina febbraio
    genera_movimenti_ricorrenti(db, oggi=date(2026, 2, 20))
    assert len(mov.list()) == 1  # NON ricreato (watermark è avanti)


def test_template_disattivo_non_genera(db: Path):
    c = _crea(db, data_inizio="2026-01-01")
    repo = ContabilitaCostoRicorrenteRepository(db_path=db)
    repo.update(c.id, nome=c.nome, categoria_id=None, importo="1200",
                cadenza="mensile", giorno_mese="1", data_inizio="2026-01-01",
                attivo=False)
    s = genera_movimenti_ricorrenti(db, oggi=date(2026, 6, 1))
    assert s.movimenti_creati == 0
