"""Test stampa PDF calendario (/calendario/stampa): tabella settimanale in pagina orizzontale."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from io import BytesIO

from docx import Document

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.pratica_eventi_repository import PraticaEventiRepository
from lys_workflow_hub.core.utenti_repository import UtentiRepository
from lys_workflow_hub.core.wincar_repository import (
    Cliente, CompagniaCliente, Controparte, Pratica, Sinistro, Veicolo,
)
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes import (
    _genera_docx_calendario_stampa,
    _titolo_settimana_stampa,
    get_app_settings,
    get_repository,
)
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_as

OPERATORE_EMAIL = "operatore-cal@test.local"
OPERATORE_PASSWORD = "test-password-1234"


def _sample_pratica(numero: int) -> Pratica:
    return Pratica(
        numero=numero,
        data_creazione=datetime(2026, 5, 1, 0, 0),
        cliente=Cliente("ROSSI MARIO", None, None, None, None, None, None, None, None, None),
        veicolo=Veicolo("AB123CD", "Fiat", "Panda", None),
        sinistro=Sinistro(None, None, None, None, None, None, None),
        controparte=Controparte(None, None, None, None, None, None, None, None),
        assicurazione_cliente=CompagniaCliente(None, None, None, None, None, None, None),
    )


def _testo_tabella(doc: Document) -> str:
    tabella = doc.tables[0]
    return "\n".join(cella.text for riga in tabella.rows for cella in riga.cells)


def test_titolo_settimana_stampa_stesso_mese():
    assert _titolo_settimana_stampa(date(2026, 9, 14), date(2026, 9, 20)) == "Settimana 14–20 Settembre 2026"


def test_titolo_settimana_stampa_a_cavallo_di_mese():
    titolo = _titolo_settimana_stampa(date(2026, 9, 28), date(2026, 10, 4))
    assert titolo == "Settimana 28 Settembre – 4 Ottobre 2026"


def test_titolo_settimana_stampa_a_cavallo_di_anno():
    titolo = _titolo_settimana_stampa(date(2026, 12, 29), date(2027, 1, 4))
    assert titolo == "Settimana 29 Dicembre 2026 – 4 Gennaio 2027"


def test_genera_docx_calendario_stampa_contenuto():
    per_giorno = {
        date(2026, 9, 15): [
            {
                "evento": _EventoFake(766, "Perizia"),
                "cliente": "Rossi Mario",
                "targa": "AB123CD",
                "marca": "Fiat",
                "modello": "Panda",
            },
        ],
    }
    docx_bytes = _genera_docx_calendario_stampa(per_giorno, "Settimana 14–20 Settembre 2026")
    doc = Document(BytesIO(docx_bytes))

    intestazione = "\n".join(p.text for p in doc.paragraphs)
    assert "Settimana 14–20 Settembre 2026" in intestazione
    # Titolo con font ingrandito rispetto al default "Heading 1".
    titolo_run = doc.paragraphs[0].runs[0]
    assert titolo_run.font.size.pt == 24

    # Pagina orizzontale
    sezione = doc.sections[0]
    assert sezione.page_width > sezione.page_height

    testo_tabella = _testo_tabella(doc)
    assert "15/09/2026" in testo_tabella
    assert "Perizia" in testo_tabella
    assert "Rossi Mario" in testo_tabella
    assert "Fiat Panda (AB123CD)" in testo_tabella
    assert "pratica 766" in testo_tabella

    # Font intestazione colonne e dati ingranditi.
    tabella = doc.tables[0]
    header_run = tabella.rows[0].cells[0].paragraphs[0].runs[0]
    assert header_run.font.size.pt == 14
    dato_run = tabella.rows[1].cells[0].paragraphs[0].runs[0]
    assert dato_run.font.size.pt == 13

    # Pagina A4 esplicita (non il default Letter di python-docx) e area utile
    # coerente con le larghezze colonna fisse, altrimenti la tabella sborda
    # dal margine su carta reale. Confronto approssimato: il round-trip
    # cm -> twips (unità XML) -> EMU non è esatto al singolo EMU.
    assert abs(sezione.page_width.cm - 29.7) < 0.05
    assert abs(sezione.page_height.cm - 21.0) < 0.05
    area_utile = sezione.page_width - sezione.left_margin - sezione.right_margin
    larghezza_tabella = sum(cella.width for cella in doc.tables[0].rows[0].cells)
    assert larghezza_tabella <= area_utile


def test_genera_docx_calendario_stampa_veicolo_parziale_o_assente():
    per_giorno = {
        date(2026, 9, 16): [
            {
                "evento": _EventoFake(700, "Solo targa"),
                "cliente": "Verdi Anna",
                "targa": "ZZ111ZZ",
                "marca": "",
                "modello": "",
            },
            {
                "evento": _EventoFake(701, "Nessun veicolo"),
                "cliente": "Neri Luca",
                "targa": "",
                "marca": "",
                "modello": "",
            },
        ],
    }
    docx_bytes = _genera_docx_calendario_stampa(per_giorno, "Settimana 14–20 Settembre 2026")
    doc = Document(BytesIO(docx_bytes))
    testo_tabella = _testo_tabella(doc)
    assert "ZZ111ZZ" in testo_tabella
    # Riga senza targa/marca/modello: fallback "—" nella cella veicolo.
    righe = doc.tables[0].rows
    riga_senza_veicolo = next(r for r in righe if "Nessun veicolo" in r.cells[3].text)
    assert riga_senza_veicolo.cells[2].text == "—"


def test_genera_docx_calendario_stampa_settimana_vuota():
    docx_bytes = _genera_docx_calendario_stampa({}, "Settimana 14–20 Settembre 2026")
    doc = Document(BytesIO(docx_bytes))
    testo = "\n".join(p.text for p in doc.paragraphs)
    assert "Nessun appuntamento" in testo
    assert doc.tables == []


class _EventoFake:
    def __init__(self, pratica_numero: int, titolo: str):
        self.pratica_numero = pratica_numero
        self.titolo = titolo


@pytest.fixture
def client_stampa(tmp_path: Path, authenticated_app: UtentiRepository):
    settings = Settings(app_db_path=tmp_path / "app.db", wincar_archivio=tmp_path)
    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    # Martedì della settimana di riferimento nei test: 2026-09-15 (Ma) è
    # nella settimana lun 2026-09-14 -> dom 2026-09-20.
    eventi_repo.add(766, "Perizia danni", date(2026, 9, 15), creato_da=1, creato_da_nome="Admin")

    wincar_repo = MagicMock()
    wincar_repo.get_pratica.return_value = _sample_pratica(766)

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_repository] = lambda: wincar_repo
    try:
        yield TestClient(app), settings
    finally:
        app.dependency_overrides.pop(get_app_settings, None)
        app.dependency_overrides.pop(get_repository, None)


def test_calendario_stampa_admin_ok(client_stampa):
    client, _ = client_stampa
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with patch("lys_workflow_hub.web.routes.docx_bytes_to_pdf_bytes", return_value=b"%PDF-fake") as mock_pdf:
        resp = client.get("/calendario/stampa", params={"settimana": "2026-09-15"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "appuntamenti_2026-09-14_2026-09-20.pdf" in resp.headers["content-disposition"]
    mock_pdf.assert_called_once()


def test_calendario_stampa_settimana_non_lunedi_calcola_i_confini(client_stampa):
    """`settimana` accetta qualunque giorno della settimana target, non solo il lunedì."""
    client, _ = client_stampa
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with patch("lys_workflow_hub.web.routes.docx_bytes_to_pdf_bytes", return_value=b"%PDF-fake") as mock_pdf:
        resp = client.get("/calendario/stampa", params={"settimana": "2026-09-20"})  # domenica
    assert resp.status_code == 200
    assert "appuntamenti_2026-09-14_2026-09-20.pdf" in resp.headers["content-disposition"]
    mock_pdf.assert_called_once()


def test_calendario_stampa_data_non_valida(client_stampa):
    client, _ = client_stampa
    login_as(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = client.get("/calendario/stampa", params={"settimana": "non-una-data"})
    assert resp.status_code == 400


def test_calendario_stampa_negata_a_non_admin(client_stampa):
    client, _ = client_stampa
    app.state.utenti_repo.create(
        email=OPERATORE_EMAIL, password=OPERATORE_PASSWORD, nome="Operatore Test", ruolo="operatore"
    )
    login_as(client, OPERATORE_EMAIL, OPERATORE_PASSWORD)
    resp = client.get("/calendario/stampa", params={"settimana": "2026-09-15"})
    assert resp.status_code == 403
