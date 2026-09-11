"""Test pagina admin /targa (ricerca veicolo per targa, API Automotive Openapi)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.utenti_repository import UtentiRepository
from lys_workflow_hub.integrations.automotive import (
    FakeAutomotiveClient,
    OpenapiAutomotiveClient,
    build_automotive_client,
    normalizza_targa,
)
from lys_workflow_hub.main import app
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_as

OPERATORE_EMAIL = "operatore-targa@test.local"
OPERATORE_PASSWORD = "test-password-1234"


def test_normalizza_targa():
    assert normalizza_targa(" ab 123-cd ") == "AB123CD"
    assert normalizza_targa("") == ""


def test_fake_client_non_trova_nulla():
    client = FakeAutomotiveClient()
    esito = client.cerca_veicolo("AB123CD")
    assert esito.trovato is False
    assert esito.targa == "AB123CD"


def test_build_automotive_client_default_fake():
    settings = Settings(_env_file=None)
    assert isinstance(build_automotive_client(settings), FakeAutomotiveClient)


def test_build_automotive_client_openapi_riusa_sdi_api_key():
    settings = Settings(_env_file=None, automotive_provider="openapi", sdi_api_key="sdi-key-123")
    client = build_automotive_client(settings)
    assert isinstance(client, OpenapiAutomotiveClient)
    assert client.api_key == "sdi-key-123"
    assert client.base_url == "https://test.automotive.openapi.com"  # automotive_test_mode default True


def test_openapi_client_parsing_schema_reale(monkeypatch):
    """Schema di risposta confermato in produzione l'11/09/2026 (vedi commit)."""
    import sys

    class _FakeResp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {
                "success": True,
                "message": "",
                "error": None,
                "data": {
                    "LicensePlate": "HA202KX",
                    "CarMake": "Dacia",
                    "CarModel": "Sandero",
                    "Version": "Sandero Streetway 1.0 tce Essential Eco-g 100cv 5 marce",
                    "BodyStyle": "Berlina",
                    "FuelType": "",
                    "Transmission": "",
                    "NumberOfDoors": "5",
                    "PowerCV": 0,
                    "RegistrationDate": "01/07/2025",
                    "Vin": "UU1DJF00875088535",
                },
            }

    def _fake_get(url, headers=None, timeout=None):
        assert url.endswith("/IT-car/HA202KX")
        return _FakeResp()

    fake_requests = type("_FakeRequestsModule", (), {"get": staticmethod(_fake_get)})
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    client = OpenapiAutomotiveClient(api_key="tok")
    esito = client.cerca_veicolo("ha202kx")
    assert esito.trovato is True
    assert esito.marca == "Dacia"
    assert esito.modello == "Sandero"
    assert esito.carrozzeria == "Berlina"
    assert esito.porte == "5"
    assert esito.potenza_cv == ""  # 0 = non disponibile
    assert esito.telaio == "UU1DJF00875088535"


def test_targa_form_admin_ok(authenticated_app: UtentiRepository):
    client = TestClient(app)
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = client.get("/targa")
    assert resp.status_code == 200
    assert "Ricerca veicolo per targa" in resp.text


def test_targa_ricerca_non_trovata_mostra_esito(authenticated_app: UtentiRepository):
    client = TestClient(app)
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = client.get("/targa", params={"targa": "AB123CD"})
    assert resp.status_code == 200
    assert "nessun dato disponibile" in resp.text.lower()


def test_targa_negata_a_non_admin(authenticated_app: UtentiRepository):
    authenticated_app.create(
        email=OPERATORE_EMAIL, password=OPERATORE_PASSWORD, nome="Operatore Test", ruolo="operatore"
    )
    client = TestClient(app)
    login_as(client, OPERATORE_EMAIL, OPERATORE_PASSWORD)
    resp = client.get("/targa")
    assert resp.status_code == 403
