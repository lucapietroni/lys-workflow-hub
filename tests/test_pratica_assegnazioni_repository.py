"""Test di `PraticaAssegnazioniRepository` (v3.0 fase 3)."""
from __future__ import annotations

from pathlib import Path

from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)


def test_assegna_e_list_per_pratica(tmp_path: Path) -> None:
    repo = PraticaAssegnazioniRepository(db_path=tmp_path / "a.db")
    repo.assegna(pratica_numero=766, utente_id=1, assegnato_da=99)
    repo.assegna(pratica_numero=766, utente_id=2, assegnato_da=99)

    assert repo.list_utente_ids_per_pratica(766) == [1, 2]
    assert repo.list_utente_ids_per_pratica(999) == []


def test_assegna_idempotente(tmp_path: Path) -> None:
    repo = PraticaAssegnazioniRepository(db_path=tmp_path / "a.db")
    repo.assegna(766, 1, assegnato_da=99)
    repo.assegna(766, 1, assegnato_da=99)  # stesso utente due volte, no errore
    assert repo.list_utente_ids_per_pratica(766) == [1]


def test_list_per_utente(tmp_path: Path) -> None:
    repo = PraticaAssegnazioniRepository(db_path=tmp_path / "a.db")
    repo.assegna(766, 1, assegnato_da=99)
    repo.assegna(800, 1, assegnato_da=99)
    repo.assegna(800, 2, assegnato_da=99)

    assert set(repo.list_pratica_numeri_per_utente(1)) == {766, 800}
    assert repo.list_pratica_numeri_per_utente(2) == [800]
    assert repo.list_pratica_numeri_per_utente(3) == []


def test_rimuovi(tmp_path: Path) -> None:
    repo = PraticaAssegnazioniRepository(db_path=tmp_path / "a.db")
    repo.assegna(766, 1, assegnato_da=99)

    assert repo.rimuovi(766, 1) is True
    assert repo.list_utente_ids_per_pratica(766) == []
    assert repo.rimuovi(766, 1) is False  # già rimossa, non rialza errore
