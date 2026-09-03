"""Test del repository SQLite per le categorie di contabilità gestionale."""
from __future__ import annotations

from pathlib import Path

import pytest

from lys_workflow_hub.core.contabilita_categoria_repository import (
    ContabilitaCategoriaRepository,
)
from lys_workflow_hub.core.contabilita_movimento_repository import (
    ContabilitaMovimentoRepository,
)


@pytest.fixture
def repo(tmp_path: Path) -> ContabilitaCategoriaRepository:
    return ContabilitaCategoriaRepository(db_path=tmp_path / "test.db")


def test_seed_categorie_al_primo_avvio(repo: ContabilitaCategoriaRepository):
    tutte = repo.list_all()
    assert len(tutte) >= 8
    nomi = {c.nome for c in tutte}
    assert {"Ricambi", "Manodopera", "Affitto", "Riparazioni carrozzeria"} <= nomi
    assert any(c.tipo == "ricavo" for c in tutte)
    assert any(c.tipo == "costo" for c in tutte)


def test_seed_non_riparte_su_db_esistente(tmp_path: Path):
    p = tmp_path / "test.db"
    r1 = ContabilitaCategoriaRepository(db_path=p)
    n = len(r1.list_all())
    r2 = ContabilitaCategoriaRepository(db_path=p)
    assert len(r2.list_all()) == n


def test_migra_note_di_credito_in_nota_di_credito(tmp_path: Path):
    import sqlite3

    p = tmp_path / "test.db"
    ContabilitaCategoriaRepository(db_path=p)
    # simula il vecchio nome
    with sqlite3.connect(p) as conn:
        conn.execute(
            "UPDATE contabilita_categoria SET nome = 'Note di credito' "
            "WHERE nome = 'Nota di credito'"
        )
    r = ContabilitaCategoriaRepository(db_path=p)
    nomi = {c.nome for c in r.list_all()}
    assert "Nota di credito" in nomi and "Note di credito" not in nomi


def test_create_categoria(repo: ContabilitaCategoriaRepository):
    c = repo.create(nome="Smaltimento rifiuti", tipo="costo")
    assert c.id is not None
    assert c.tipo == "costo"
    assert c.attiva is True
    assert repo.get(c.id).nome == "Smaltimento rifiuti"


def test_create_rifiuta_nome_vuoto_o_tipo_errato(repo: ContabilitaCategoriaRepository):
    with pytest.raises(ValueError, match="nome"):
        repo.create(nome="  ", tipo="costo")
    with pytest.raises(ValueError, match="[Tt]ipo"):
        repo.create(nome="X", tipo="patrimoniale")


def test_create_rifiuta_duplicato_case_insensitive(repo: ContabilitaCategoriaRepository):
    repo.create(nome="Cancelleria", tipo="costo")
    with pytest.raises(ValueError, match="già"):
        repo.create(nome="cancelleria", tipo="costo")


def test_update_e_set_attiva(repo: ContabilitaCategoriaRepository):
    c = repo.create(nome="Tinteggiatura", tipo="costo")
    up = repo.update(c.id, nome="Verniciatura extra", tipo="costo", attiva=False)
    assert up.nome == "Verniciatura extra"
    assert up.attiva is False
    repo.set_attiva(c.id, True)
    assert repo.get(c.id).attiva is True


def test_list_solo_attive(repo: ContabilitaCategoriaRepository):
    c = repo.create(nome="Obsoleta", tipo="costo")
    repo.set_attiva(c.id, False)
    attive_ids = {x.id for x in repo.list_all(solo_attive=True)}
    assert c.id not in attive_ids
    assert c.id in {x.id for x in repo.list_all()}


def test_delete_categoria_libera(repo: ContabilitaCategoriaRepository):
    c = repo.create(nome="Temporanea", tipo="costo")
    assert repo.delete(c.id) is True
    assert repo.get(c.id) is None


def test_delete_bloccato_se_usata_da_movimento(tmp_path: Path):
    p = tmp_path / "test.db"
    cat_repo = ContabilitaCategoriaRepository(db_path=p)
    mov_repo = ContabilitaMovimentoRepository(db_path=p)
    c = cat_repo.create(nome="In uso", tipo="costo")
    mov_repo.create(data="2026-01-10", importo="100", tipo="uscita", categoria_id=c.id)
    assert cat_repo.delete(c.id) is False
    assert cat_repo.get(c.id) is not None
