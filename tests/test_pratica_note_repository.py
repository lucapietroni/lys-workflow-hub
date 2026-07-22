"""Test di PraticaNoteRepository (v3.0 fase 4)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lys_workflow_hub.core.pratica_note_repository import PraticaNoteRepository


def test_add_e_list_ordine_cronologico(tmp_path: Path) -> None:
    repo = PraticaNoteRepository(db_path=tmp_path / "note.db")
    repo.add(766, 1, "Admin", "preso app.to con perito")
    repo.add(766, 2, "Agenzia", "servono foto lavorazione")

    note = repo.list_per_pratica(766)
    assert [n.testo for n in note] == ["preso app.to con perito", "servono foto lavorazione"]
    assert note[0].autore_nome == "Admin"
    assert note[1].utente_id == 2


def test_list_filtra_per_pratica(tmp_path: Path) -> None:
    repo = PraticaNoteRepository(db_path=tmp_path / "note.db")
    repo.add(766, 1, "Admin", "nota su 766")
    repo.add(999, 1, "Admin", "nota su 999")

    assert [n.testo for n in repo.list_per_pratica(766)] == ["nota su 766"]


def test_add_testo_vuoto_raises(tmp_path: Path) -> None:
    repo = PraticaNoteRepository(db_path=tmp_path / "note.db")
    with pytest.raises(ValueError):
        repo.add(766, 1, "Admin", "   ")


def test_list_pratica_senza_note_vuota(tmp_path: Path) -> None:
    repo = PraticaNoteRepository(db_path=tmp_path / "note.db")
    assert repo.list_per_pratica(766) == []
