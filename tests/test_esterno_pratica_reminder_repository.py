"""Test di EsternoPraticaReminderRepository (reminder ricorrente lato esterno,
simmetrico a test_admin_pratica_reminder_repository.py)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from lys_workflow_hub.core.esterno_pratica_reminder_repository import (
    STATO_ATTIVO,
    EsternoPraticaReminderRepository,
)


def test_upsert_attivo_crea_reminder(tmp_path: Path) -> None:
    repo = EsternoPraticaReminderRepository(db_path=tmp_path / "reminder.db")
    repo.upsert_attivo(766, titolo="Nuova nota", messaggio="Admin: preso app.to")

    attivi = repo.list_attivi()
    assert len(attivi) == 1
    assert attivi[0].pratica_numero == 766
    assert attivi[0].titolo == "Nuova nota"
    assert attivi[0].stato == STATO_ATTIVO
    assert attivi[0].creato_il is not None
    assert attivi[0].ultimo_promemoria_il is not None


def test_upsert_attivo_ripetuto_non_duplica_e_aggiorna_testo(tmp_path: Path) -> None:
    repo = EsternoPraticaReminderRepository(db_path=tmp_path / "reminder.db")
    repo.upsert_attivo(766, titolo="Nuova nota", messaggio="primo messaggio")
    repo.upsert_attivo(766, titolo="Nuova nota", messaggio="secondo messaggio")

    attivi = repo.list_attivi()
    assert len(attivi) == 1
    assert attivi[0].messaggio == "secondo messaggio"


def test_upsert_attivo_ripetuto_non_resetta_ultimo_promemoria(tmp_path: Path) -> None:
    """Un admin che tocca ripetutamente la stessa pratica non deve poter
    posticipare all'infinito il resend — solo il testo si aggiorna."""
    db_path = tmp_path / "reminder.db"
    repo = EsternoPraticaReminderRepository(db_path=db_path)
    repo.upsert_attivo(766, titolo="Nuova nota", messaggio="primo")

    vecchio = (datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE esterno_pratica_reminder SET ultimo_promemoria_il = ? WHERE pratica_numero = 766",
            (vecchio,),
        )
        conn.commit()

    repo.upsert_attivo(766, titolo="Nuova nota", messaggio="secondo")

    attivi = repo.list_attivi()
    assert attivi[0].ultimo_promemoria_il.isoformat(timespec="seconds") == vecchio


def test_indice_parziale_impedisce_due_reminder_attivi_per_pratica(tmp_path: Path) -> None:
    db_path = tmp_path / "reminder.db"
    repo = EsternoPraticaReminderRepository(db_path=db_path)
    repo.upsert_attivo(766, titolo="A", messaggio="a")

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO esterno_pratica_reminder "
                "(pratica_numero, titolo, messaggio, stato, creato_il, ultimo_promemoria_il) "
                "VALUES (766, 'B', 'b', 'attivo', 'x', 'x')"
            )


def test_upsert_attivo_pratiche_diverse_non_si_mescolano(tmp_path: Path) -> None:
    repo = EsternoPraticaReminderRepository(db_path=tmp_path / "reminder.db")
    repo.upsert_attivo(766, titolo="A", messaggio="a")
    repo.upsert_attivo(999, titolo="B", messaggio="b")

    attivi = {r.pratica_numero: r for r in repo.list_attivi()}
    assert set(attivi) == {766, 999}


def test_list_scaduti_rispetta_soglia(tmp_path: Path) -> None:
    db_path = tmp_path / "reminder.db"
    repo = EsternoPraticaReminderRepository(db_path=db_path)
    repo.upsert_attivo(766, titolo="Recente", messaggio="m")
    repo.upsert_attivo(999, titolo="Vecchio", messaggio="m")

    vecchio = (datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE esterno_pratica_reminder SET ultimo_promemoria_il = ? WHERE pratica_numero = 999",
            (vecchio,),
        )
        conn.commit()

    scaduti = repo.list_scaduti(soglia_ore=24)
    assert [r.pratica_numero for r in scaduti] == [999]


def test_segna_rimandato_aggiorna_timestamp_e_esce_da_scaduti(tmp_path: Path) -> None:
    db_path = tmp_path / "reminder.db"
    repo = EsternoPraticaReminderRepository(db_path=db_path)
    repo.upsert_attivo(766, titolo="T", messaggio="m")
    vecchio = (datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE esterno_pratica_reminder SET ultimo_promemoria_il = ? WHERE pratica_numero = 766",
            (vecchio,),
        )
        conn.commit()

    assert len(repo.list_scaduti(soglia_ore=24)) == 1
    rem_id = repo.list_scaduti(soglia_ore=24)[0].id
    repo.segna_rimandato(rem_id)

    assert repo.list_scaduti(soglia_ore=24) == []


def test_risolvi_per_pratica_marca_risolto(tmp_path: Path) -> None:
    repo = EsternoPraticaReminderRepository(db_path=tmp_path / "reminder.db")
    repo.upsert_attivo(766, titolo="T", messaggio="m")
    repo.risolvi_per_pratica(766, risolto_da="azione")

    assert repo.list_attivi() == []


def test_risolvi_per_pratica_senza_reminder_attivo_e_no_op(tmp_path: Path) -> None:
    repo = EsternoPraticaReminderRepository(db_path=tmp_path / "reminder.db")
    repo.risolvi_per_pratica(766, risolto_da="azione")  # nessuna eccezione
    assert repo.list_attivi() == []


def test_dopo_risoluzione_nuova_attivita_ricrea_reminder(tmp_path: Path) -> None:
    repo = EsternoPraticaReminderRepository(db_path=tmp_path / "reminder.db")
    repo.upsert_attivo(766, titolo="Prima", messaggio="m1")
    repo.risolvi_per_pratica(766, risolto_da="azione")
    assert repo.list_attivi() == []

    repo.upsert_attivo(766, titolo="Seconda", messaggio="m2")
    attivi = repo.list_attivi()
    assert len(attivi) == 1
    assert attivi[0].titolo == "Seconda"
    assert attivi[0].stato == STATO_ATTIVO


def test_admin_e_esterno_reminder_su_stesso_db_non_collidono(tmp_path: Path) -> None:
    """Stesso file DB di AdminPraticaReminderRepository (settings.app_db_path
    condiviso): gli indici devono avere nomi distinti per tabella, altrimenti
    CREATE INDEX IF NOT EXISTS su un nome già preso da admin salterebbe la
    creazione dell'indice esterno e romperebbe l'unicità del reminder attivo."""
    from lys_workflow_hub.core.admin_pratica_reminder_repository import (
        AdminPraticaReminderRepository,
    )

    db_path = tmp_path / "shared.db"
    admin_repo = AdminPraticaReminderRepository(db_path=db_path)
    esterno_repo = EsternoPraticaReminderRepository(db_path=db_path)

    admin_repo.upsert_attivo(766, titolo="Admin", messaggio="m")
    esterno_repo.upsert_attivo(766, titolo="Esterno", messaggio="m")

    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO esterno_pratica_reminder "
                "(pratica_numero, titolo, messaggio, stato, creato_il, ultimo_promemoria_il) "
                "VALUES (766, 'B', 'b', 'attivo', 'x', 'x')"
            )

    assert len(admin_repo.list_attivi()) == 1
    assert len(esterno_repo.list_attivi()) == 1
