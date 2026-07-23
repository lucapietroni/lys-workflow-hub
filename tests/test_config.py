"""Test di Settings.public_url()."""
from __future__ import annotations

from lys_workflow_hub.config import Settings


def test_public_url_usa_public_base_url_se_impostato(tmp_path) -> None:
    settings = Settings(
        wincar_archivio=tmp_path,
        public_base_url="https://hub.lysauto.it",
    )
    assert settings.public_url("/pratiche/766") == "https://hub.lysauto.it/pratiche/766"


def test_public_url_toglie_trailing_slash_da_public_base_url(tmp_path) -> None:
    settings = Settings(
        wincar_archivio=tmp_path,
        public_base_url="https://hub.lysauto.it/",
    )
    assert settings.public_url("/pratiche/766") == "https://hub.lysauto.it/pratiche/766"


def test_public_url_fallback_con_app_host_reale(tmp_path) -> None:
    settings = Settings(
        wincar_archivio=tmp_path,
        public_base_url="",
        app_host="192.168.1.42",
        app_port=8000,
    )
    assert settings.public_url("/pratiche/766") == "http://192.168.1.42:8000/pratiche/766"


def test_public_url_fallback_con_app_host_0000_usa_localhost(tmp_path) -> None:
    """Regressione: APP_HOST=0.0.0.0 (bind su tutte le interfacce, non un
    indirizzo navigabile) senza PUBLIC_BASE_URL impostato produceva link
    tipo http://0.0.0.0:8000/... nelle notifiche push — inutilizzabile dal
    telefono. Bug reale segnalato in produzione."""
    settings = Settings(
        wincar_archivio=tmp_path,
        public_base_url="",
        app_host="0.0.0.0",
        app_port=8000,
    )
    assert settings.public_url("/pratiche/766") == "http://localhost:8000/pratiche/766"
