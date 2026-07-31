"""Test delle note/calendario condivisi su pratica (v3.0 fase 4).

Copre sia il lato admin (/pratiche/{numero}/note, /eventi) sia il lato
esterno (/portale/pratiche/{numero}/note, /eventi), incluso il controllo di
accesso (esterno non assegnato -> 404) e la protezione IDOR sulla
cancellazione di un evento di un'altra pratica.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lys_workflow_hub.config import Settings
from lys_workflow_hub.core.pratica_assegnazioni_repository import (
    PraticaAssegnazioniRepository,
)
from lys_workflow_hub.core.pratica_eventi_repository import PraticaEventiRepository
from lys_workflow_hub.core.pratica_note_repository import PraticaNoteRepository
from lys_workflow_hub.core.pratica_stato_repository import PraticaStatoRepository
from lys_workflow_hub.core.wincar_repository import (
    Cliente,
    CompagniaCliente,
    Controparte,
    Pratica,
    Sinistro,
    Veicolo,
)
from lys_workflow_hub.main import app
from lys_workflow_hub.web.routes import get_app_settings, get_repository
from lys_workflow_hub.web.routes_portale import (
    get_assegnazioni_repo,
    get_portale_settings,
    get_wincar_repo,
)
from tests.conftest import get_csrf, login_as, login_as_admin


def _jpeg_bytes(size: tuple[int, int] = (49, 88)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(120, 40, 10)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _sample_pratica(numero: int = 766) -> Pratica:
    return Pratica(
        numero=numero,
        data_creazione=datetime(2026, 5, 1, 0, 0),
        cliente=Cliente("ROSSI MARIO", None, None, None, None, None, None, None, None, None),
        veicolo=Veicolo("AB123CD", None, None, None),
        sinistro=Sinistro(None, None, None, None, None, None, None),
        controparte=Controparte(None, None, None, None, None, None, None, None),
        assicurazione_cliente=CompagniaCliente(None, None, None, None, None, None, None),
    )


@pytest.fixture
def admin_client(tmp_path: Path, authenticated_app):
    repo = MagicMock()
    repo.get_pratica.return_value = _sample_pratica()
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")

    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_app_settings] = lambda: settings
    client = TestClient(app, follow_redirects=False)
    login_as_admin(client)
    try:
        yield client, settings
    finally:
        app.dependency_overrides.pop(get_repository, None)
        app.dependency_overrides.pop(get_app_settings, None)


@pytest.fixture
def portale_setup(tmp_path: Path, authenticated_app):
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=tmp_path / "app.db")
    wincar_repo = MagicMock()
    wincar_repo.get_pratica.side_effect = lambda n: _sample_pratica(n) if n == 766 else None
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")

    app.dependency_overrides[get_assegnazioni_repo] = lambda: assegnazioni_repo
    app.dependency_overrides[get_wincar_repo] = lambda: wincar_repo
    app.dependency_overrides[get_portale_settings] = lambda: settings
    try:
        yield assegnazioni_repo, settings
    finally:
        app.dependency_overrides.pop(get_assegnazioni_repo, None)
        app.dependency_overrides.pop(get_wincar_repo, None)
        app.dependency_overrides.pop(get_portale_settings, None)


# --------------------------------------------------------------------------- #
#  Lato admin
# --------------------------------------------------------------------------- #


def test_admin_aggiunge_nota_e_evento(admin_client) -> None:
    client, _ = admin_client
    token = get_csrf(client, "/pratiche/766")

    resp = client.post(
        "/pratiche/766/note", data={"testo": "servono foto lavorazione", "csrf_token": token}
    )
    assert resp.status_code == 303

    resp = client.post(
        "/pratiche/766/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
    )
    assert resp.status_code == 303

    resp = client.get("/pratiche/766")
    assert "servono foto lavorazione" in resp.text
    assert "Perizia" in resp.text
    assert "05/08/2026" in resp.text


def test_admin_feed_attivita_mostra_nota_e_evento_recenti(admin_client) -> None:
    client, _ = admin_client
    token = get_csrf(client, "/pratiche/766")

    client.post(
        "/pratiche/766/note", data={"testo": "servono foto lavorazione", "csrf_token": token}
    )
    client.post(
        "/pratiche/766/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
    )

    resp = client.get("/pratiche/766")
    assert resp.status_code == 200
    assert 'id="attivita"' in resp.text
    assert "Attività recenti" in resp.text
    assert "ha scritto una nota" in resp.text
    assert "servono foto lavorazione" in resp.text
    assert "ha aggiunto in calendario" in resp.text
    assert "Perizia" in resp.text and "05/08/2026" in resp.text


def test_admin_feed_attivita_non_tronca_storico_stato_a_5(admin_client) -> None:
    """`pratica_stato_storia` (widget "Storico ultimi cambi") è tagliato a 5
    voci — il feed deve avere il proprio storico non tagliato, altrimenti un
    cambio stato più vecchio del 5° verrebbe scartato prima ancora di poter
    competere per un posto tra le 15 voci più recenti del feed."""
    client, settings = admin_client
    stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
    for i in range(6):
        stato_repo.set_stato(766, "in_gestione", changed_by=f"Admin{i}")

    resp = client.get("/pratiche/766")
    assert resp.status_code == 200
    assert resp.text.count("ha cambiato lo stato") == 6


def test_admin_elimina_evento(admin_client) -> None:
    client, _ = admin_client
    token = get_csrf(client, "/pratiche/766")
    client.post(
        "/pratiche/766/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
    )
    resp = client.get("/pratiche/766")
    assert "Perizia" in resp.text

    match = re.search(r'/pratiche/766/eventi/(\d+)/elimina', resp.text)
    assert match, "azione di eliminazione evento non trovata"
    resp = client.post(match.group(0), data={"csrf_token": token})
    assert resp.status_code == 303

    resp = client.get("/pratiche/766")
    assert "Nessun evento in calendario" in resp.text


def test_admin_modifica_nota(admin_client) -> None:
    client, settings = admin_client
    token = get_csrf(client, "/pratiche/766")
    note_repo = PraticaNoteRepository(db_path=settings.app_db_path)
    nota = note_repo.add(766, 1, "Admin", "testo originale")

    resp = client.post(
        f"/pratiche/766/note/{nota.id}/modifica",
        data={"testo": "testo corretto", "csrf_token": token},
    )
    assert resp.status_code == 303
    assert note_repo.list_per_pratica(766)[0].testo == "testo corretto"


def test_admin_elimina_nota(admin_client) -> None:
    client, settings = admin_client
    token = get_csrf(client, "/pratiche/766")
    note_repo = PraticaNoteRepository(db_path=settings.app_db_path)
    nota = note_repo.add(766, 1, "Admin", "da eliminare")

    resp = client.post(f"/pratiche/766/note/{nota.id}/elimina", data={"csrf_token": token})
    assert resp.status_code == 303
    assert note_repo.list_per_pratica(766) == []


def test_admin_non_puo_modificare_nota_di_altra_pratica(admin_client) -> None:
    """IDOR: la nota appartiene alla pratica 999, l'URL punta a 766."""
    client, settings = admin_client
    token = get_csrf(client, "/pratiche/766")
    note_repo = PraticaNoteRepository(db_path=settings.app_db_path)
    nota = note_repo.add(999, 1, "Admin", "nota riservata")

    client.post(
        f"/pratiche/766/note/{nota.id}/modifica",
        data={"testo": "manomessa", "csrf_token": token},
    )
    assert note_repo.list_per_pratica(999)[0].testo == "nota riservata"


def test_admin_calendario_mostra_eventi_di_tutte_le_pratiche(admin_client) -> None:
    client, settings = admin_client
    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    oggi = date.today()
    eventi_repo.add(766, "Perizia 766", oggi, 1, "Admin")
    eventi_repo.add(999, "Perizia 999", oggi, 1, "Admin")

    resp = client.get(f"/calendario?anno={oggi.year}&mese={oggi.month}")
    assert resp.status_code == 200
    assert "Perizia 766" in resp.text
    assert "Perizia 999" in resp.text


def test_admin_calendario_naviga_mese_senza_eventi(admin_client) -> None:
    client, _ = admin_client
    resp = client.get("/calendario?anno=2020&mese=1")
    assert resp.status_code == 200
    assert "Gennaio 2020" in resp.text


# --------------------------------------------------------------------------- #
#  Lato portale (esterno)
# --------------------------------------------------------------------------- #


def test_portale_list_ordina_per_numero_decrescente(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    for numero in (100, 300, 200):
        assegnazioni_repo.assegna(numero, esterno.id, assegnato_da=1)

    wincar_repo = app.dependency_overrides[get_wincar_repo]()
    wincar_repo.get_pratica.side_effect = lambda n: _sample_pratica(n)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")
    resp = client.get("/portale")
    assert resp.status_code == 200
    testo = resp.text
    assert (
        testo.index('data-numero="300"')
        < testo.index('data-numero="200"')
        < testo.index('data-numero="100"')
    )


def test_portale_detail_richiede_assegnazione(authenticated_app, portale_setup) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 404


def test_portale_detail_e_collaborazione_utente_assegnato(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert "ROSSI MARIO" in resp.text
    token = get_csrf(client, "/portale/pratiche/766")

    resp = client.post(
        "/portale/pratiche/766/note",
        data={"testo": "preso app.to con perito", "csrf_token": token},
    )
    assert resp.status_code == 303

    resp = client.post(
        "/portale/pratiche/766/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
    )
    assert resp.status_code == 303

    resp = client.get("/portale/pratiche/766")
    assert "preso app.to con perito" in resp.text
    assert "Perizia" in resp.text


def test_portale_feed_attivita_mostra_nota_e_evento_recenti(
    authenticated_app, portale_setup
) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")
    token = get_csrf(client, "/portale/pratiche/766")

    client.post(
        "/portale/pratiche/766/note",
        data={"testo": "preso app.to con perito", "csrf_token": token},
    )
    client.post(
        "/portale/pratiche/766/eventi",
        data={"titolo": "Perizia", "data_evento": "2026-08-05", "csrf_token": token},
    )

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert 'id="attivita"' in resp.text
    assert "Attività recenti" in resp.text
    assert "ha scritto una nota" in resp.text
    assert "preso app.to con perito" in resp.text
    assert "ha aggiunto in calendario" in resp.text
    assert "Perizia" in resp.text and "05/08/2026" in resp.text


def test_portale_mostra_stato_default_aperta(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert "Aperta" in resp.text
    assert "Aggiorna stato" in resp.text


def test_portale_puo_cambiare_stato_pratica_assegnata(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={
            "stato": "in_gestione",
            "note": "preso in carico",
            "csrf_token": get_csrf(client, "/portale/pratiche/766"),
        },
    )
    assert resp.status_code == 303

    stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
    stato = stato_repo.get_stato(766)
    assert stato.stato == "in_gestione"
    assert stato.changed_by == "Agenzia"
    assert stato.note == "preso in carico"


def test_portale_dropdown_stato_include_periziata(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert '<option value="periziata"' in resp.text
    assert "Periziata" in resp.text


def test_portale_puo_impostare_stato_periziata(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={"stato": "periziata", "csrf_token": get_csrf(client, "/portale/pratiche/766")},
    )
    assert resp.status_code == 303

    stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
    assert stato_repo.get_stato(766).stato == "periziata"


def test_portale_dropdown_stato_include_in_trattativa(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert '<option value="in_trattativa"' in resp.text
    assert "In trattativa" in resp.text


def test_portale_puo_impostare_stato_in_trattativa(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={"stato": "in_trattativa", "csrf_token": get_csrf(client, "/portale/pratiche/766")},
    )
    assert resp.status_code == 303

    stato_repo = PraticaStatoRepository(db_path=settings.app_db_path)
    assert stato_repo.get_stato(766).stato == "in_trattativa"


def test_portale_cambia_stato_rifiuta_valore_non_valido(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, _ = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={"stato": "non-esiste", "csrf_token": get_csrf(client, "/portale/pratiche/766")},
    )
    assert resp.status_code == 400


def test_portale_non_assegnato_non_puo_cambiare_stato(authenticated_app, portale_setup) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/stato",
        data={"stato": "chiusa", "csrf_token": get_csrf(client, "/portale")},
    )
    assert resp.status_code == 404


def test_portale_non_assegnato_non_puo_scrivere_note(authenticated_app, portale_setup) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/note",
        data={"testo": "non dovrei poterlo fare", "csrf_token": get_csrf(client, "/portale")},
    )
    assert resp.status_code == 404


def test_portale_foto_pratica_serve_file_reale_non_403(authenticated_app, portale_setup) -> None:
    """Regressione: gli URL di foto/documenti nella pagina portale devono
    puntare a `/portale/pratiche/{numero}/file` (verifica assegnazione), non
    a `/pratiche/{numero}/file` (admin-only) — altrimenti un esterno assegnato
    riceve 403 aprendo una foto della propria pratica."""
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    foto_dir.mkdir(parents=True)
    (foto_dir / "danno.jpg").write_bytes(b"fake-jpeg-bytes")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766")
    assert resp.status_code == 200
    assert "/portale/pratiche/766/file?path=" in resp.text
    assert 'src="/pratiche/766/file?path=' not in resp.text  # non l'URL admin-only

    match = re.search(r'src="(/portale/pratiche/766/file\?path=[^"]+)"', resp.text)
    assert match, "URL foto non trovato in pagina"

    resp = client.get(match.group(1))
    assert resp.status_code == 200
    assert resp.content == b"fake-jpeg-bytes"


def test_portale_foto_zip_scarica_tutte(authenticated_app, portale_setup) -> None:
    """Senza `path` in query, /foto/zip zippa tutte le foto della pratica."""
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    foto_dir.mkdir(parents=True)
    (foto_dir / "danno1.jpg").write_bytes(b"foto-1")
    (foto_dir / "danno2.jpg").write_bytes(b"foto-2")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766/foto/zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert set(zf.namelist()) == {"danno1.jpg", "danno2.jpg"}


def test_portale_foto_zip_scarica_selezionate(authenticated_app, portale_setup) -> None:
    """Con `path` valorizzato, /foto/zip zippa solo le foto richieste."""
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    foto_dir.mkdir(parents=True)
    (foto_dir / "danno1.jpg").write_bytes(b"foto-1")
    scelta = foto_dir / "danno2.jpg"
    scelta.write_bytes(b"foto-2")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766/foto/zip", params={"path": str(scelta)})
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert zf.namelist() == ["danno2.jpg"]
    assert zf.read("danno2.jpg") == b"foto-2"


def test_portale_foto_zip_ignora_path_esterno_alla_pratica(authenticated_app, portale_setup) -> None:
    """Un path che non appartiene alle foto della pratica (IDOR) viene
    ignorato, non solleva un file arbitrario dal filesystem."""
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    foto_dir.mkdir(parents=True)
    (foto_dir / "danno1.jpg").write_bytes(b"foto-1")

    fuori_pratica = settings.wincar_archivio / "segreto.jpg"
    fuori_pratica.write_bytes(b"non-autorizzato")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get("/portale/pratiche/766/foto/zip", params={"path": str(fuori_pratica)})
    assert resp.status_code == 400

    resp = client.get(
        "/portale/pratiche/766/foto/zip",
        params={"path": [str(fuori_pratica), str(foto_dir / "danno1.jpg")]},
    )
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert zf.namelist() == ["danno1.jpg"]


def test_admin_foto_zip_scarica_tutte(admin_client) -> None:
    client, settings = admin_client
    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    foto_dir.mkdir(parents=True)
    (foto_dir / "danno1.jpg").write_bytes(b"foto-1")

    resp = client.get("/pratiche/766/foto/zip")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert zf.namelist() == ["danno1.jpg"]


def test_portale_non_puo_eliminare_evento_di_altra_pratica(authenticated_app, portale_setup) -> None:
    """IDOR: un esterno assegnato alla pratica 766 non deve poter cancellare
    un evento che appartiene alla pratica 999, anche indovinandone l'id."""
    assegnazioni_repo, settings = portale_setup
    esterno_a = authenticated_app.create(
        email="a@esempio.it", password="password1234", nome="A", ruolo="esterno"
    )
    esterno_b = authenticated_app.create(
        email="b@esempio.it", password="password1234", nome="B", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno_a.id, assegnato_da=1)
    assegnazioni_repo.assegna(999, esterno_b.id, assegnato_da=1)

    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    evento_999 = eventi_repo.add(999, "Perizia riservata", date(2026, 8, 5), esterno_b.id, "B")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "a@esempio.it", "password1234")

    # 766 è assegnata ad A: la route risponde 303 (non 403/404 sulla richiesta
    # in sé, perché A ha accesso a 766), ma l'evento di 999 resta intatto.
    resp = client.post(
        f"/portale/pratiche/766/eventi/{evento_999.id}/elimina",
        data={"csrf_token": get_csrf(client, "/portale/pratiche/766")},
    )
    assert resp.status_code == 303
    assert eventi_repo.list_per_pratica(999) != []


def test_portale_calendario_mostra_solo_eventi_pratiche_assegnate(
    authenticated_app, portale_setup
) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    eventi_repo = PraticaEventiRepository(db_path=settings.app_db_path)
    oggi = date.today()
    eventi_repo.add(766, "Mia perizia", oggi, 1, "Admin")
    eventi_repo.add(999, "Perizia altrui", oggi, 1, "Admin")

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.get(f"/portale/calendario?anno={oggi.year}&mese={oggi.month}")
    assert resp.status_code == 200
    assert "Mia perizia" in resp.text
    assert "Perizia altrui" not in resp.text


# --------------------------------------------------------------------------- #
#  Upload foto/documenti dal portale (v3.0 fase 6)
# --------------------------------------------------------------------------- #


def test_portale_upload_foto_salva_nella_cartella_wincar(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/foto",
        data={"csrf_token": get_csrf(client, "/portale/pratiche/766")},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/portale/pratiche/766?upload_ok=1")

    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    salvati = list(foto_dir.glob("danno_*.jpg"))
    assert len(salvati) == 1
    assert salvati[0].read_bytes() == b"fake-jpeg-bytes"


def test_portale_upload_documento_salva_negli_allegati(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/documenti",
        data={"csrf_token": get_csrf(client, "/portale/pratiche/766")},
        files={"files": ("preventivo.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/portale/pratiche/766?upload_ok=1")

    allegati_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Allegati"
    salvati = list(allegati_dir.glob("preventivo_*.pdf"))
    assert len(salvati) == 1


def test_portale_upload_rifiuta_formato_non_supportato(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/documenti",
        data={"csrf_token": get_csrf(client, "/portale/pratiche/766")},
        files={"files": ("virus.exe", b"MZ-fake-binary", "application/octet-stream")},
    )
    assert resp.status_code == 303
    assert "errori=1" in resp.headers["location"]

    allegati_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Allegati"
    assert not allegati_dir.exists() or list(allegati_dir.iterdir()) == []


def test_portale_non_assegnato_non_puo_uploadare_foto(authenticated_app, portale_setup) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/foto",
        data={"csrf_token": get_csrf(client, "/portale")},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 404


def test_portale_upload_rifiuta_troppi_file_in_una_richiesta(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    troppi_file = [
        ("files", (f"foto{i}.jpg", b"fake-jpeg-bytes", "image/jpeg")) for i in range(21)
    ]
    resp = client.post(
        "/portale/pratiche/766/foto",
        data={"csrf_token": get_csrf(client, "/portale/pratiche/766")},
        files=troppi_file,
    )
    assert resp.status_code == 400


def test_portale_upload_richiede_csrf_valido(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/foto",
        data={"csrf_token": "token-invalido"},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 403


def test_admin_upload_foto_salva_nella_cartella_wincar(admin_client) -> None:
    client, settings = admin_client
    resp = client.post(
        "/pratiche/766/foto",
        data={"csrf_token": get_csrf(client, "/pratiche/766")},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/pratiche/766?upload_ok=1")

    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    salvati = list(foto_dir.glob("danno_*.jpg"))
    assert len(salvati) == 1
    assert salvati[0].read_bytes() == b"fake-jpeg-bytes"


def test_admin_upload_documento_salva_negli_allegati(admin_client) -> None:
    client, settings = admin_client
    resp = client.post(
        "/pratiche/766/documenti",
        data={"csrf_token": get_csrf(client, "/pratiche/766")},
        files={"files": ("preventivo.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/pratiche/766?upload_ok=1")

    allegati_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Allegati"
    salvati = list(allegati_dir.glob("preventivo_*.pdf"))
    assert len(salvati) == 1


def test_admin_elimina_foto_rimuove_file_e_thumb(admin_client) -> None:
    client, settings = admin_client
    resp = client.post(
        "/pratiche/766/foto",
        data={"csrf_token": get_csrf(client, "/pratiche/766")},
        files={"files": ("danno.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    salvata = list(foto_dir.glob("danno_*.jpg"))[0]
    thumb = salvata.with_name(salvata.name + ".thumb")
    # Byte JPEG reali (non un placeholder): il thumb DEVE esistere prima
    # della cancellazione, altrimenti l'assert sotto sarebbe vacuamente vera
    # (non proverebbe che la route lo elimina davvero).
    assert thumb.exists()

    resp = client.post(
        "/pratiche/766/foto/elimina",
        data={"csrf_token": get_csrf(client, "/pratiche/766"), "path": str(salvata)},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/pratiche/766#foto"
    assert not salvata.exists()
    assert not thumb.exists()


def test_admin_elimina_foto_toglie_anche_il_frame_da_thumbs_thumb(admin_client) -> None:
    """Regressione: senza questo, dopo un'eliminazione WinCar continuava a
    mostrare la miniatura di una foto non più esistente (segnalato
    dall'utente) — l'indice condiviso non si aggiorna da solo togliendo il
    file su disco, va rimosso esplicitamente il frame corrispondente."""
    client, settings = admin_client
    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"

    for nome in ("danno1.jpg", "danno2.jpg"):
        client.post(
            "/pratiche/766/foto",
            data={"csrf_token": get_csrf(client, "/pratiche/766")},
            files={"files": (nome, _jpeg_bytes(), "image/jpeg")},
        )
    indice = foto_dir / "Thumbs.thumb"
    with Image.open(indice) as im:
        assert im.n_frames == 2

    salvata1 = list(foto_dir.glob("danno1_*.jpg"))[0]
    salvata2 = list(foto_dir.glob("danno2_*.jpg"))[0]
    client.post(
        "/pratiche/766/foto/elimina",
        data={"csrf_token": get_csrf(client, "/pratiche/766"), "path": str(salvata1)},
    )

    with Image.open(indice) as im:
        assert im.n_frames == 1
        assert im.tag_v2.get(270) == salvata2.name + ".thumb"


def test_admin_elimina_foto_azzera_f_foto_su_ultima_rimasta(
    admin_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, settings = admin_client
    client.post(
        "/pratiche/766/foto",
        data={"csrf_token": get_csrf(client, "/pratiche/766")},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    salvata = list(foto_dir.glob("danno_*.jpg"))[0]

    chiamate = []
    monkeypatch.setattr(
        "lys_workflow_hub.web.routes.marca_foto_assente",
        lambda **kwargs: chiamate.append(kwargs),
    )
    client.post(
        "/pratiche/766/foto/elimina",
        data={"csrf_token": get_csrf(client, "/pratiche/766"), "path": str(salvata)},
    )
    assert len(chiamate) == 1
    assert chiamate[0]["numero_pratica"] == 766


def test_admin_elimina_foto_non_azzera_f_foto_se_ne_restano_altre(
    admin_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, settings = admin_client
    for nome in ("danno1.jpg", "danno2.jpg"):
        client.post(
            "/pratiche/766/foto",
            data={"csrf_token": get_csrf(client, "/pratiche/766")},
            files={"files": (nome, b"fake-jpeg-bytes", "image/jpeg")},
        )
    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    prima = list(foto_dir.glob("danno1_*.jpg"))[0]

    chiamate = []
    monkeypatch.setattr(
        "lys_workflow_hub.web.routes.marca_foto_assente",
        lambda **kwargs: chiamate.append(kwargs),
    )
    client.post(
        "/pratiche/766/foto/elimina",
        data={"csrf_token": get_csrf(client, "/pratiche/766"), "path": str(prima)},
    )
    assert chiamate == []


def test_admin_elimina_foto_rifiuta_path_di_unaltra_pratica(admin_client) -> None:
    client, settings = admin_client
    fuori_pratica = settings.wincar_archivio / "segreto.jpg"
    fuori_pratica.write_bytes(b"non-autorizzato")

    resp = client.post(
        "/pratiche/766/foto/elimina",
        data={"csrf_token": get_csrf(client, "/pratiche/766"), "path": str(fuori_pratica)},
    )
    assert resp.status_code == 403
    assert fuori_pratica.exists()


def test_esterno_non_puo_eliminare_foto(authenticated_app, portale_setup) -> None:
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/pratiche/766/foto/elimina",
        data={"csrf_token": get_csrf(client, "/portale"), "path": "/qualsiasi/percorso.jpg"},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
#  Eliminazione file caricati da un esterno (solo i propri, mai quelli
#  dell'admin o di un altro collaboratore) — vedi
#  PraticaFileUploaderRepository.
# --------------------------------------------------------------------------- #


def test_esterno_elimina_propria_foto(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")
    token = get_csrf(client, "/portale/pratiche/766")

    resp = client.post(
        "/portale/pratiche/766/foto",
        data={"csrf_token": token},
        files={"files": ("danno.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 303
    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    salvata = list(foto_dir.glob("danno_*.jpg"))[0]
    assert salvata.exists()

    # Il bottone di eliminazione deve comparire per questo file (l'ha
    # caricato lei) nella pagina di dettaglio.
    assert 'class="foto-elimina"' in client.get("/portale/pratiche/766").text

    resp = client.post(
        "/portale/pratiche/766/foto/elimina",
        data={"csrf_token": token, "path": str(salvata)},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portale/pratiche/766#foto"
    assert not salvata.exists()


def test_esterno_non_puo_eliminare_foto_caricata_da_admin(
    admin_client, authenticated_app, portale_setup
) -> None:
    admin_c, admin_settings = admin_client
    assegnazioni_repo, portale_settings = portale_setup
    # admin_client e portale_setup condividono lo stesso tmp_path in questo
    # stesso test (fixture pytest risolte una sola volta per test) — stessa
    # cartella WinCar/app.db, verificato esplicitamente per non dare per
    # scontato un dettaglio implementativo delle fixture.
    assert admin_settings.wincar_archivio == portale_settings.wincar_archivio

    admin_c.post(
        "/pratiche/766/foto",
        data={"csrf_token": get_csrf(admin_c, "/pratiche/766")},
        files={"files": ("danno.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    foto_dir = admin_settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    salvata = list(foto_dir.glob("danno_*.jpg"))[0]
    assert salvata.exists()

    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")
    token = get_csrf(client, "/portale/pratiche/766")

    # Niente bottone di eliminazione per un file non suo.
    assert 'class="foto-elimina"' not in client.get("/portale/pratiche/766").text

    resp = client.post(
        "/portale/pratiche/766/foto/elimina",
        data={"csrf_token": token, "path": str(salvata)},
    )
    assert resp.status_code == 403
    assert salvata.exists()


def test_esterno_non_puo_eliminare_foto_di_un_altro_esterno(
    authenticated_app, portale_setup
) -> None:
    assegnazioni_repo, settings = portale_setup
    primo = authenticated_app.create(
        email="primo@esempio.it", password="password1234", nome="Primo", ruolo="esterno"
    )
    secondo = authenticated_app.create(
        email="secondo@esempio.it", password="password1234", nome="Secondo", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, primo.id, assegnato_da=1)
    assegnazioni_repo.assegna(766, secondo.id, assegnato_da=1)

    client_primo = TestClient(app, follow_redirects=False)
    login_as(client_primo, "primo@esempio.it", "password1234")
    client_primo.post(
        "/portale/pratiche/766/foto",
        data={"csrf_token": get_csrf(client_primo, "/portale/pratiche/766")},
        files={"files": ("danno.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    foto_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Foto"
    salvata = list(foto_dir.glob("danno_*.jpg"))[0]

    client_secondo = TestClient(app, follow_redirects=False)
    login_as(client_secondo, "secondo@esempio.it", "password1234")
    resp = client_secondo.post(
        "/portale/pratiche/766/foto/elimina",
        data={
            "csrf_token": get_csrf(client_secondo, "/portale/pratiche/766"),
            "path": str(salvata),
        },
    )
    assert resp.status_code == 403
    assert salvata.exists()


def test_esterno_elimina_proprio_documento(authenticated_app, portale_setup) -> None:
    assegnazioni_repo, settings = portale_setup
    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)

    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")
    token = get_csrf(client, "/portale/pratiche/766")

    client.post(
        "/portale/pratiche/766/documenti",
        data={"csrf_token": token},
        files={"files": ("doc.pdf", b"%PDF-fake", "application/pdf")},
    )
    allegati_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Allegati"
    salvato = list(allegati_dir.glob("doc_*.pdf"))[0]
    assert salvato.exists()

    resp = client.post(
        "/portale/pratiche/766/documenti/elimina",
        data={"csrf_token": token, "path": str(salvato)},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portale/pratiche/766#documenti"
    assert not salvato.exists()


def test_esterno_non_puo_eliminare_documento_caricato_da_admin(
    admin_client, authenticated_app, portale_setup
) -> None:
    admin_c, admin_settings = admin_client
    assegnazioni_repo, _ = portale_setup

    admin_c.post(
        "/pratiche/766/documenti",
        data={"csrf_token": get_csrf(admin_c, "/pratiche/766")},
        files={"files": ("doc.pdf", b"%PDF-fake", "application/pdf")},
    )
    allegati_dir = admin_settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Allegati"
    salvato = list(allegati_dir.glob("doc_*.pdf"))[0]

    esterno = authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/portale/pratiche/766/documenti/elimina",
        data={"csrf_token": get_csrf(client, "/portale/pratiche/766"), "path": str(salvato)},
    )
    assert resp.status_code == 403
    assert salvato.exists()


def test_esterno_non_puo_eliminare_file_di_unaltra_pratica_assegnata(
    tmp_path: Path, authenticated_app
) -> None:
    """IDOR: stesso esterno assegnato a due pratiche (766 e 767). Carica un
    file sulla 767 e prova a eliminarlo passando per l'URL della 766 con lo
    stesso path — deve restare 403. Blocca la regressione se in futuro
    `_richiedi_proprietario_file`/`_elimina_foto_fisica` venissero disaccoppiate
    (oggi `PraticaFileUploaderRepository.eliminabile_da` verifica ESPLICITAMENTE
    che `pratica_numero` combaci, non solo `caricato_da`)."""
    assegnazioni_repo = PraticaAssegnazioniRepository(db_path=tmp_path / "app.db")
    wincar_repo = MagicMock()
    wincar_repo.get_pratica.side_effect = (
        lambda n: _sample_pratica(n) if n in (766, 767) else None
    )
    settings = Settings(wincar_archivio=tmp_path, app_db_path=tmp_path / "app.db")

    app.dependency_overrides[get_assegnazioni_repo] = lambda: assegnazioni_repo
    app.dependency_overrides[get_wincar_repo] = lambda: wincar_repo
    app.dependency_overrides[get_portale_settings] = lambda: settings
    try:
        esterno = authenticated_app.create(
            email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
        )
        assegnazioni_repo.assegna(766, esterno.id, assegnato_da=1)
        assegnazioni_repo.assegna(767, esterno.id, assegnato_da=1)

        client = TestClient(app, follow_redirects=False)
        login_as(client, "agenzia@esempio.it", "password1234")

        client.post(
            "/portale/pratiche/767/foto",
            data={"csrf_token": get_csrf(client, "/portale/pratiche/767")},
            files={"files": ("danno.jpg", _jpeg_bytes(), "image/jpeg")},
        )
        foto_dir = settings.wincar_archivio / "Pratiche" / "767" / "Pubblici" / "Foto"
        salvata = list(foto_dir.glob("danno_*.jpg"))[0]
        assert salvata.exists()

        resp = client.post(
            "/portale/pratiche/766/foto/elimina",
            data={
                "csrf_token": get_csrf(client, "/portale/pratiche/766"),
                "path": str(salvata),
            },
        )
        assert resp.status_code == 403
        assert salvata.exists()
    finally:
        app.dependency_overrides.pop(get_assegnazioni_repo, None)
        app.dependency_overrides.pop(get_wincar_repo, None)
        app.dependency_overrides.pop(get_portale_settings, None)


def test_admin_upload_rifiuta_formato_non_supportato(admin_client) -> None:
    client, settings = admin_client
    resp = client.post(
        "/pratiche/766/documenti",
        data={"csrf_token": get_csrf(client, "/pratiche/766")},
        files={"files": ("virus.exe", b"MZ-fake-binary", "application/octet-stream")},
    )
    assert resp.status_code == 303
    assert "errori=1" in resp.headers["location"]

    allegati_dir = settings.wincar_archivio / "Pratiche" / "766" / "Pubblici" / "Allegati"
    assert not allegati_dir.exists() or list(allegati_dir.iterdir()) == []


def test_admin_upload_richiede_csrf_valido(admin_client) -> None:
    client, _ = admin_client
    resp = client.post(
        "/pratiche/766/foto",
        data={"csrf_token": "token-invalido"},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 403


def test_admin_upload_richiede_login(authenticated_app) -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        "/pratiche/766/foto",
        data={"csrf_token": "qualsiasi"},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_esterno_non_puo_uploadare_su_route_admin(authenticated_app, portale_setup) -> None:
    """route admin-only (/pratiche/... senza /portale) deve rifiutare un
    esterno anche se loggato, non solo un utente anonimo."""
    authenticated_app.create(
        email="agenzia@esempio.it", password="password1234", nome="Agenzia", ruolo="esterno"
    )
    client = TestClient(app, follow_redirects=False)
    login_as(client, "agenzia@esempio.it", "password1234")

    resp = client.post(
        "/pratiche/766/foto",
        data={"csrf_token": get_csrf(client, "/portale")},
        files={"files": ("danno.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 403
