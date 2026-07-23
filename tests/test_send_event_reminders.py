"""Test dello script schedulato dei reminder "il giorno prima" (v3.0 fase 5,
parte B). Esercita `run_once()` end-to-end contro un DB temporaneo, con
`notify_*` mockate (mai toccare rete reale nei test)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from lys_workflow_hub.config import get_settings
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.pratica_eventi_repository import PraticaEventiRepository
from lys_workflow_hub.core.utenti_repository import UtentiRepository

import scripts.send_event_reminders as reminders


@pytest.fixture
def settings_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Punta `get_settings()` (usata internamente da `run_once()`, non
    iniettabile) a un DB/log temporanei, poi ripristina la cache alla fine
    per non far trapelare stato tra test."""
    db_path = tmp_path / "app.db"
    log_path = tmp_path / "logs" / "app.log"
    monkeypatch.setenv("APP_DB_PATH", str(db_path))
    monkeypatch.setenv("APP_LOG_PATH", str(log_path))
    monkeypatch.setenv("WINCAR_ARCHIVIO", str(tmp_path / "wincar"))
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.sh")
    monkeypatch.setenv("NTFY_TOPIC", "topic-admin")
    monkeypatch.setenv("NOTIFY_DISABLED", "false")
    get_settings.cache_clear()
    try:
        yield db_path
    finally:
        get_settings.cache_clear()


def test_reminder_notifica_admin_e_esterno(settings_env: Path) -> None:
    db_path = settings_env
    eventi_repo = PraticaEventiRepository(db_path=db_path)
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=db_path)
    utenti_repo = UtentiRepository(db_path=db_path)

    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    utenti_repo.set_notifiche(
        esterno.id,
        notify_email_enabled=True,
        notify_push_enabled=True,
        ntfy_topic="topic-agenzia",
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)
    evento = eventi_repo.add(766, "Perizia", date.today() + timedelta(days=1), 1, "Admin")

    with (
        patch("scripts.send_event_reminders.notify_push_nuova_attivita") as mock_push,
        patch("scripts.send_event_reminders.notify_esterno_nuova_attivita") as mock_email,
    ):
        rc = reminders.run_once()

    assert rc == 0
    assert mock_push.call_count == 2  # admin + esterno (push attivo)
    topics = {c.kwargs["ntfy_topic"] for c in mock_push.call_args_list}
    assert topics == {"topic-admin", "topic-agenzia"}
    mock_email.assert_called_once()
    assert mock_email.call_args.kwargs["recipient"] == "agenzia@esempio.it"
    assert eventi_repo.reminder_gia_inviato(evento.id) is True


def test_reminder_non_rimanda_se_gia_notificato(settings_env: Path) -> None:
    db_path = settings_env
    eventi_repo = PraticaEventiRepository(db_path=db_path)
    evento = eventi_repo.add(766, "Perizia", date.today() + timedelta(days=1), 1, "Admin")
    eventi_repo.segna_reminder_inviato(evento.id)

    with patch("scripts.send_event_reminders.notify_push_nuova_attivita") as mock_push:
        rc = reminders.run_once()

    assert rc == 0
    mock_push.assert_not_called()


def test_reminder_nessun_evento_domani_non_notifica(settings_env: Path) -> None:
    db_path = settings_env
    eventi_repo = PraticaEventiRepository(db_path=db_path)
    eventi_repo.add(766, "Tra una settimana", date.today() + timedelta(days=7), 1, "Admin")

    with patch("scripts.send_event_reminders.notify_push_nuova_attivita") as mock_push:
        rc = reminders.run_once()

    assert rc == 0
    mock_push.assert_not_called()


def test_reminder_esterno_senza_preferenze_attive_non_notificato(settings_env: Path) -> None:
    db_path = settings_env
    eventi_repo = PraticaEventiRepository(db_path=db_path)
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=db_path)
    utenti_repo = UtentiRepository(db_path=db_path)

    esterno = utenti_repo.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    utenti_repo.set_notifiche(
        esterno.id, notify_email_enabled=False, notify_push_enabled=False, ntfy_topic=""
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)
    eventi_repo.add(766, "Perizia", date.today() + timedelta(days=1), 1, "Admin")

    with (
        patch("scripts.send_event_reminders.notify_push_nuova_attivita") as mock_push,
        patch("scripts.send_event_reminders.notify_esterno_nuova_attivita") as mock_email,
    ):
        rc = reminders.run_once()

    assert rc == 0
    mock_push.assert_called_once()  # solo l'admin
    mock_email.assert_not_called()


def test_reminder_un_evento_fallito_non_blocca_gli_altri(settings_env: Path) -> None:
    """Regressione: un errore su un evento (es. sqlite3.OperationalError
    transitorio sul DB condiviso con l'app in esecuzione) non deve bloccare
    l'intero ciclo — `list_domani()` fa un match di data ESATTO, quindi un
    evento saltato oggi non verrebbe più ritentato domani."""
    db_path = settings_env
    eventi_repo = PraticaEventiRepository(db_path=db_path)
    domani = date.today() + timedelta(days=1)
    evento_ko = eventi_repo.add(111, "Fallisce", domani, 1, "Admin")
    evento_ok = eventi_repo.add(222, "Va a buon fine", domani, 1, "Admin")

    def _side_effect(evento, **kwargs):
        if evento.pratica_numero == 111:
            raise RuntimeError("errore simulato")

    with patch("scripts.send_event_reminders._notifica_evento", side_effect=_side_effect):
        rc = reminders.run_once()

    assert rc == 0
    assert eventi_repo.reminder_gia_inviato(evento_ko.id) is False
    assert eventi_repo.reminder_gia_inviato(evento_ok.id) is True


def test_reminder_lock_impedisce_esecuzioni_sovrapposte(settings_env: Path, tmp_path: Path) -> None:
    lock_path = tmp_path / "logs" / "event_reminders.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("99999", encoding="utf-8")

    rc = reminders.run_once()
    assert rc == 2
