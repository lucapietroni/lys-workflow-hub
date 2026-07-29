"""Test di IngressiOfficinaRepository."""
from __future__ import annotations

from pathlib import Path

import pytest

from lys_workflow_hub.core.ingressi_officina_repository import (
    STATO_ANNULLATO,
    STATO_COLLEGATO,
    STATO_IN_ATTESA,
    IngressiOfficinaRepository,
)


def test_crea_e_get(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="Mario Rossi", targa="AB123CD", note="urto posteriore", creato_da=1)

    assert ingresso.cliente_nominativo == "Mario Rossi"
    assert ingresso.targa == "AB123CD"
    assert ingresso.stato == STATO_IN_ATTESA
    assert ingresso.file == ()

    ripreso = repo.get(ingresso.id)
    assert ripreso is not None
    assert ripreso.cliente_nominativo == "Mario Rossi"


def test_crea_senza_nominativo_raises(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    with pytest.raises(ValueError):
        repo.crea(cliente_nominativo="   ", targa="", note="", creato_da=1)


def test_get_inesistente_none(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    assert repo.get(999) is None


def test_list_per_stato_filtra(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    a = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)
    repo.crea(cliente_nominativo="B", targa="", note="", creato_da=1)
    repo.collega(a.id, numero_pratica_wincar=100, collegato_da=2)

    in_attesa = repo.list_per_stato(STATO_IN_ATTESA)
    assert [i.cliente_nominativo for i in in_attesa] == ["B"]

    collegati = repo.list_per_stato(STATO_COLLEGATO)
    assert [i.cliente_nominativo for i in collegati] == ["A"]


def test_count_in_attesa(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    assert repo.count_in_attesa() == 0
    repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)
    repo.crea(cliente_nominativo="B", targa="", note="", creato_da=1)
    assert repo.count_in_attesa() == 2


def test_aggiungi_file_e_get_lo_include(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)
    repo.aggiungi_file(ingresso.id, tipo="cid", nome_file="cid_123.pdf", nome_file_originale="cid.pdf")

    ripreso = repo.get(ingresso.id)
    assert len(ripreso.file) == 1
    assert ripreso.file[0].tipo == "cid"
    assert ripreso.file[0].nome_file_originale == "cid.pdf"
    assert ripreso.file[0].categoria_upload == "documento"


def test_aggiungi_file_foto_danno_categoria_upload_foto(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)
    f = repo.aggiungi_file(
        ingresso.id, tipo="foto_danno", nome_file="danno_1.jpg", nome_file_originale="IMG.jpg"
    )
    assert f.categoria_upload == "foto"


def test_aggiungi_file_tipo_non_valido_raises(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)
    with pytest.raises(ValueError):
        repo.aggiungi_file(ingresso.id, tipo="non-valido", nome_file="x", nome_file_originale="x")


def test_elimina_file_rimuove_e_ritorna_record(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)
    f = repo.aggiungi_file(ingresso.id, tipo="cid", nome_file="cid_1.pdf", nome_file_originale="cid.pdf")

    eliminato = repo.elimina_file(f.id, ingresso.id)
    assert eliminato is not None
    assert eliminato.id == f.id
    assert repo.get(ingresso.id).file == ()


def test_elimina_file_ingresso_sbagliato_non_elimina(tmp_path: Path) -> None:
    """IDOR: non deve essere possibile eliminare il file di un altro
    ingresso passando un `ingresso_id` diverso da quello reale."""
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)
    f = repo.aggiungi_file(ingresso.id, tipo="cid", nome_file="cid_1.pdf", nome_file_originale="cid.pdf")

    eliminato = repo.elimina_file(f.id, 999)
    assert eliminato is None
    assert len(repo.get(ingresso.id).file) == 1


def test_collega_imposta_stato_e_numero(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)

    ok = repo.collega(ingresso.id, numero_pratica_wincar=766, collegato_da=2)
    assert ok is True

    ripreso = repo.get(ingresso.id)
    assert ripreso.stato == STATO_COLLEGATO
    assert ripreso.numero_pratica_wincar == 766
    assert ripreso.collegato_da == 2
    assert ripreso.collegato_il is not None


def test_collega_due_volte_seconda_fallisce(tmp_path: Path) -> None:
    """Solo un ingresso `in_attesa` può essere collegato — evita che due
    admin colleghino lo stesso ingresso concorrentemente a numeri diversi."""
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)
    repo.collega(ingresso.id, numero_pratica_wincar=766, collegato_da=2)

    ok = repo.collega(ingresso.id, numero_pratica_wincar=999, collegato_da=3)
    assert ok is False
    assert repo.get(ingresso.id).numero_pratica_wincar == 766


def test_annulla_imposta_stato(tmp_path: Path) -> None:
    repo = IngressiOfficinaRepository(db_path=tmp_path / "ingressi.db")
    ingresso = repo.crea(cliente_nominativo="A", targa="", note="", creato_da=1)

    ok = repo.annulla(ingresso.id)
    assert ok is True
    assert repo.get(ingresso.id).stato == STATO_ANNULLATO
