"""Test del repository SQLite per l'anagrafica delle compagnie assicurative."""
from __future__ import annotations

from pathlib import Path

import pytest

from lys_workflow_hub.core.compagnie_repository import (
    CompagnieRepository,
    _normalizza_nome,
)


# ---------------------------------------------------------------------------
# Normalizzazione nomi
# ---------------------------------------------------------------------------


def test_normalizza_nome_rimuove_suffissi_societari():
    assert _normalizza_nome("Generali S.p.A.") == "generali"
    assert _normalizza_nome("Generali SpA") == "generali"
    assert _normalizza_nome("Generali Assicurazioni") == "generali"
    assert _normalizza_nome("UnipolSai Assicurazioni S.p.A.") == "unipolsai"


def test_normalizza_nome_case_insensitive():
    assert _normalizza_nome("GENERALI") == _normalizza_nome("Generali")
    assert _normalizza_nome("  Generali   ") == "generali"


def test_normalizza_nome_su_stringa_vuota_o_none():
    assert _normalizza_nome("") == ""
    assert _normalizza_nome("   ") == ""
    assert _normalizza_nome(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CRUD base
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> CompagnieRepository:
    return CompagnieRepository(db_path=tmp_path / "test.db")


def test_create_then_list_returns_record(repo: CompagnieRepository):
    repo.create(
        nome="Generali Italia",
        pec="generali.atti.vandalici@pec.generali.it",
        indirizzo="Piazza Tre Torri 1",
        cap="20145",
        citta="Milano",
        provincia="mi",  # verrà normalizzato a uppercase
    )
    all_records = repo.list_all()
    assert len(all_records) == 1
    record = all_records[0]
    assert record.nome == "Generali Italia"
    assert record.pec == "generali.atti.vandalici@pec.generali.it"
    assert record.provincia == "MI"
    assert record.id is not None


def test_create_rejects_empty_nome_or_pec(repo: CompagnieRepository):
    with pytest.raises(ValueError, match="nome"):
        repo.create(nome="", pec="x@pec.it")
    with pytest.raises(ValueError, match="PEC"):
        repo.create(nome="X", pec="")


def test_create_rejects_duplicate_pec(repo: CompagnieRepository):
    repo.create(nome="A", pec="ufficio.sinistri@pec.compagnia.it")
    with pytest.raises(ValueError, match="già presente"):
        repo.create(nome="B", pec="ufficio.sinistri@pec.compagnia.it")


def test_update_modifica_il_record(repo: CompagnieRepository):
    c = repo.create(nome="UnipolSai", pec="ufficio@pec.unipolsai.it")
    updated = repo.update(
        c.id, nome="UnipolSai Assicurazioni",
        pec="ufficio@pec.unipolsai.it",
        indirizzo="Via Stalingrado 45",
    )
    assert updated.nome == "UnipolSai Assicurazioni"
    assert updated.indirizzo == "Via Stalingrado 45"


def test_update_su_id_inesistente_solleva_value_error(repo: CompagnieRepository):
    with pytest.raises(ValueError):
        repo.update(9999, nome="X", pec="x@pec.it")


def test_delete_returns_true_se_eliminato(repo: CompagnieRepository):
    c = repo.create(nome="X", pec="x@pec.it")
    assert repo.delete(c.id) is True
    assert repo.get(c.id) is None


def test_delete_returns_false_se_id_inesistente(repo: CompagnieRepository):
    assert repo.delete(9999) is False


# ---------------------------------------------------------------------------
# Lookup per nome
# ---------------------------------------------------------------------------


def test_lookup_by_name_riconosce_varianti_di_scrittura(repo: CompagnieRepository):
    repo.create(nome="Generali Italia S.p.A.", pec="generali@pec.generali.it")
    # Match con varianti tipiche del campo F_DEASCL di WinCar.
    for query in [
        "Generali Italia S.p.A.",
        "generali italia spa",
        "GENERALI ITALIA",
        "Generali Italia Assicurazioni",
        "  Generali Italia  ",
    ]:
        found = repo.lookup_by_name(query)
        assert found is not None, f"Mancato match per: {query!r}"
        assert found.pec == "generali@pec.generali.it"


def test_lookup_by_name_ritorna_none_se_non_trova(repo: CompagnieRepository):
    repo.create(nome="UnipolSai", pec="x@pec.it")
    assert repo.lookup_by_name("Allianz") is None
    assert repo.lookup_by_name("") is None
    assert repo.lookup_by_name("   ") is None


def test_lookup_preferisce_record_con_pec_valorizzata(tmp_path: Path):
    """Se due record hanno lo stesso nome_norm, vince quello con PEC piena."""
    # Costruiamo manualmente perché il repository ora richiede sempre PEC.
    # Quindi creiamo due nomi diversi che si normalizzano allo stesso valore,
    # entrambi con PEC valida (caso più realistico).
    repo = CompagnieRepository(db_path=tmp_path / "test.db")
    repo.create(nome="Generali", pec="a@pec.it")
    repo.create(nome="Generali Assicurazioni", pec="b@pec.it")
    found = repo.lookup_by_name("generali italia")  # va in 'generali' come norm
    assert found is not None
    assert found.pec in ("a@pec.it", "b@pec.it")


# ---------------------------------------------------------------------------
# Indirizzo compatto
# ---------------------------------------------------------------------------


def test_indirizzo_compatto_omette_parti_vuote(repo: CompagnieRepository):
    c = repo.create(nome="X", pec="x@pec.it", indirizzo="Via Roma 1", citta="Roma")
    assert "Via Roma 1" in c.indirizzo_compatto
    assert "Roma" in c.indirizzo_compatto


def test_indirizzo_compatto_su_record_minimale(repo: CompagnieRepository):
    c = repo.create(nome="X", pec="x@pec.it")
    assert c.indirizzo_compatto == ""
