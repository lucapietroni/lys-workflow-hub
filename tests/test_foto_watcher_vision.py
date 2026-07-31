"""_call_vision deve leggere il PRIMO blocco di tipo "text" nella risposta,
non semplicemente content[0] — con extended thinking abilitato sul modello
il primo blocco può essere un ThinkingBlock (nessun attributo `.text`),
causando un AttributeError che faceva perdere la lettura targa (bug
osservato in produzione, vedi log FotoWatcher: errore Claude Vision)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.foto_lavorazioni_repository import FotoLavorazioniRepository
from lys_workflow_hub.integrations.foto_watcher import FotoWatcher


def _watcher(tmp_path: Path) -> FotoWatcher:
    settings = Settings(
        wincar_archivio=tmp_path,
        app_db_path=tmp_path / "app.db",
        anthropic_api_key="sk-fake",
    )
    foto_repo = FotoLavorazioniRepository(db_path=tmp_path / "app.db")
    return FotoWatcher(settings, foto_repo)


def test_call_vision_salta_blocco_thinking_e_legge_il_testo(tmp_path: Path) -> None:
    watcher = _watcher(tmp_path)
    watcher._ai_client = MagicMock()
    watcher._ai_client.messages.create.return_value = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="ragiono sulla targa..."),
            SimpleNamespace(type="text", text="ab123cd"),
        ]
    )

    result = watcher._call_vision(b"fake-jpeg", "image/jpeg", "leggi la targa")

    assert result == "AB123CD"


def test_call_vision_nessun_blocco_testo_ritorna_none_senza_sollevare(tmp_path: Path) -> None:
    watcher = _watcher(tmp_path)
    watcher._ai_client = MagicMock()
    watcher._ai_client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="solo pensiero, nessuna risposta")]
    )

    assert watcher._call_vision(b"fake-jpeg", "image/jpeg", "leggi la targa") is None


def test_call_vision_content_solo_testo_comportamento_invariato(tmp_path: Path) -> None:
    watcher = _watcher(tmp_path)
    watcher._ai_client = MagicMock()
    watcher._ai_client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="xy999zz")]
    )

    assert watcher._call_vision(b"fake-jpeg", "image/jpeg", "leggi la targa") == "XY999ZZ"
