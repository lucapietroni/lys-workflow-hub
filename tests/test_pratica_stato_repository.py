"""Test per PraticaStatoRepository.stati_correnti() — usato dall'export CSV."""
from __future__ import annotations

from pathlib import Path

from lys_workflow_hub.core.pratica_stato_repository import PraticaStatoRepository


def test_stati_correnti_vuoto_senza_cambi(tmp_path: Path) -> None:
    repo = PraticaStatoRepository(db_path=tmp_path / "app.db")
    assert repo.stati_correnti() == {}


def test_stati_correnti_prende_solo_ultimo_cambio_per_pratica(tmp_path: Path) -> None:
    repo = PraticaStatoRepository(db_path=tmp_path / "app.db")
    repo.set_stato(766, "in_gestione", changed_by="Admin")
    repo.set_stato(766, "in_liquidazione", changed_by="Admin")
    repo.set_stato(900, "chiusa", changed_by="Admin")

    stati = repo.stati_correnti()

    assert stati == {766: "in_liquidazione", 900: "chiusa"}


def test_stati_correnti_omette_pratiche_mai_toccate(tmp_path: Path) -> None:
    repo = PraticaStatoRepository(db_path=tmp_path / "app.db")
    repo.set_stato(766, "chiusa", changed_by="Admin")

    stati = repo.stati_correnti()

    # 999 non ha mai avuto un cambio: assente dal dict, il chiamante
    # applica il default "aperta" (non è compito del repository).
    assert 999 not in stati
    assert stati[766] == "chiusa"
