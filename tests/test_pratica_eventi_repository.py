"""Test di PraticaEventiRepository (v3.0 fase 4)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from lys_workflow_hub.core.pratica_eventi_repository import PraticaEventiRepository


def test_add_e_list_ordinato_per_data(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    repo.add(766, "Rientro", date(2026, 8, 10), 1, "Admin")
    repo.add(766, "Perizia", date(2026, 7, 30), 2, "Agenzia")

    eventi = repo.list_per_pratica(766)
    assert [e.titolo for e in eventi] == ["Perizia", "Rientro"]
    assert eventi[0].data_evento == date(2026, 7, 30)
    assert eventi[1].creato_da_nome == "Admin"


def test_list_filtra_per_pratica(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    repo.add(766, "Perizia 766", date(2026, 7, 30), 1, "Admin")
    repo.add(999, "Perizia 999", date(2026, 7, 30), 1, "Admin")

    assert [e.titolo for e in repo.list_per_pratica(766)] == ["Perizia 766"]


def test_add_titolo_vuoto_raises(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    with pytest.raises(ValueError):
        repo.add(766, "   ", date(2026, 7, 30), 1, "Admin")


def test_delete_scoped_a_pratica(tmp_path: Path) -> None:
    """delete() richiede il pratica_numero corretto: previene un IDOR dove un
    utente con accesso alla pratica A cancella un evento della pratica B."""
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    evento = repo.add(766, "Perizia", date(2026, 7, 30), 1, "Admin")

    assert repo.delete(evento.id, pratica_numero=999) is False
    assert len(repo.list_per_pratica(766)) == 1

    assert repo.delete(evento.id, pratica_numero=766) is True
    assert repo.list_per_pratica(766) == []


def test_list_prossimi_filtra_per_finestra_giorni(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    oggi = date.today()
    repo.add(766, "Domani", oggi + timedelta(days=1), 1, "Admin")
    repo.add(766, "Tra 10 giorni", oggi + timedelta(days=10), 1, "Admin")
    repo.add(766, "Ieri (passato)", oggi - timedelta(days=1), 1, "Admin")

    prossimi = repo.list_prossimi(entro_giorni=7)
    assert [e.titolo for e in prossimi] == ["Domani"]


def test_list_prossimi_filtra_per_pratiche(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    oggi = date.today()
    repo.add(766, "Mia pratica", oggi + timedelta(days=1), 1, "Admin")
    repo.add(999, "Altra pratica", oggi + timedelta(days=1), 1, "Admin")

    assert [e.titolo for e in repo.list_prossimi(pratica_numeri=[766])] == ["Mia pratica"]
    assert repo.list_prossimi(pratica_numeri=[]) == []
    assert len(repo.list_prossimi(pratica_numeri=None)) == 2


def test_list_prossimi_vuota_senza_eventi(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    assert repo.list_prossimi() == []


def test_list_mese_filtra_per_anno_mese(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    repo.add(766, "In agosto", date(2026, 8, 5), 1, "Admin")
    repo.add(766, "In luglio", date(2026, 7, 20), 1, "Admin")
    repo.add(766, "Altro agosto (bordo mese)", date(2026, 8, 31), 1, "Admin")

    eventi = repo.list_mese(2026, 8)
    assert [e.titolo for e in eventi] == ["In agosto", "Altro agosto (bordo mese)"]


def test_list_mese_filtra_per_pratiche(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    repo.add(766, "Mia pratica", date(2026, 8, 5), 1, "Admin")
    repo.add(999, "Altra pratica", date(2026, 8, 5), 1, "Admin")

    assert [e.titolo for e in repo.list_mese(2026, 8, pratica_numeri=[766])] == ["Mia pratica"]
    assert repo.list_mese(2026, 8, pratica_numeri=[]) == []
    assert len(repo.list_mese(2026, 8, pratica_numeri=None)) == 2


def test_list_domani(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    oggi = date.today()
    repo.add(766, "Domani", oggi + timedelta(days=1), 1, "Admin")
    repo.add(766, "Oggi", oggi, 1, "Admin")
    repo.add(766, "Dopodomani", oggi + timedelta(days=2), 1, "Admin")

    assert [e.titolo for e in repo.list_domani()] == ["Domani"]


def test_reminder_dedup(tmp_path: Path) -> None:
    repo = PraticaEventiRepository(db_path=tmp_path / "eventi.db")
    evento = repo.add(766, "Perizia", date.today() + timedelta(days=1), 1, "Admin")

    assert repo.reminder_gia_inviato(evento.id) is False
    repo.segna_reminder_inviato(evento.id)
    assert repo.reminder_gia_inviato(evento.id) is True

    # Idempotente: richiamarlo due volte non deve sollevare (UNIQUE index).
    repo.segna_reminder_inviato(evento.id)
    assert repo.reminder_gia_inviato(evento.id) is True
