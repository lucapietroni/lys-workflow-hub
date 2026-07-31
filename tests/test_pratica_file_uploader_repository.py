"""Test di PraticaFileUploaderRepository — traccia chi ha caricato ciascun
file di una pratica, per permettere a un esterno di eliminare solo i propri
upload (mai quelli dell'admin o di un altro collaboratore)."""
from __future__ import annotations

from pathlib import Path

from lys_workflow_hub.core.pratica_file_uploader_repository import (
    PraticaFileUploaderRepository,
)


def test_registra_e_caricato_da(tmp_path: Path) -> None:
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    path = tmp_path / "danno_20260101-000000.jpg"
    repo.registra(766, path, caricato_da=42, caricato_da_nome="Agenzia")

    assert repo.caricato_da(path) == 42


def test_caricato_da_none_per_path_sconosciuto(tmp_path: Path) -> None:
    """File caricati prima dell'introduzione di questa tracciatura (o mai
    tracciati per qualunque motivo) non hanno un proprietario noto — deve
    restituire None, non 0 o un valore che potrebbe combaciare per errore
    con un id utente reale."""
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    assert repo.caricato_da(tmp_path / "mai-tracciato.jpg") is None


def test_path_caricati_da_filtra_per_utente_e_pratica(tmp_path: Path) -> None:
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    c_altra_pratica = tmp_path / "c.jpg"
    d_altro_utente = tmp_path / "d.jpg"
    repo.registra(766, a, caricato_da=1, caricato_da_nome="Agenzia")
    repo.registra(766, b, caricato_da=1, caricato_da_nome="Agenzia")
    repo.registra(999, c_altra_pratica, caricato_da=1, caricato_da_nome="Agenzia")
    repo.registra(766, d_altro_utente, caricato_da=2, caricato_da_nome="Avvocato")

    risultato = repo.path_caricati_da(766, 1)
    assert risultato == {str(a), str(b)}


def test_rimuovi_elimina_il_tracciamento(tmp_path: Path) -> None:
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    path = tmp_path / "danno.jpg"
    repo.registra(766, path, caricato_da=1, caricato_da_nome="Agenzia")
    assert repo.caricato_da(path) == 1

    repo.rimuovi(path)
    assert repo.caricato_da(path) is None


def test_rimuovi_path_mai_tracciato_non_solleva(tmp_path: Path) -> None:
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    repo.rimuovi(tmp_path / "mai-esistito.jpg")  # nessuna eccezione


def test_eliminabile_da_true_solo_se_pratica_e_utente_combaciano(tmp_path: Path) -> None:
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    path = tmp_path / "danno.jpg"
    repo.registra(766, path, caricato_da=1, caricato_da_nome="Agenzia")

    assert repo.eliminabile_da(766, path, 1) is True


def test_eliminabile_da_false_se_numero_pratica_non_combacia(tmp_path: Path) -> None:
    """IDOR: stesso file, stesso utente, ma numero pratica diverso da quello
    tracciato — non deve bastare essere l'autore, la pratica nell'URL deve
    essere proprio quella dove il file è stato caricato."""
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    path = tmp_path / "danno.jpg"
    repo.registra(767, path, caricato_da=1, caricato_da_nome="Agenzia")

    assert repo.eliminabile_da(766, path, 1) is False


def test_eliminabile_da_false_se_utente_non_combacia(tmp_path: Path) -> None:
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    path = tmp_path / "danno.jpg"
    repo.registra(766, path, caricato_da=1, caricato_da_nome="Agenzia")

    assert repo.eliminabile_da(766, path, 2) is False


def test_eliminabile_da_false_per_path_mai_tracciato(tmp_path: Path) -> None:
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    assert repo.eliminabile_da(766, tmp_path / "mai-tracciato.jpg", 1) is False


def test_registra_stesso_path_due_volte_aggiorna_non_duplica(tmp_path: Path) -> None:
    """Non dovrebbe mai capitare in pratica (i nomi file sono sempre
    timestampati e univoci, save_upload() non sovrascrive mai), ma
    registra() non deve sollevare né lasciare righe duplicate se capita."""
    repo = PraticaFileUploaderRepository(db_path=tmp_path / "uploader.db")
    path = tmp_path / "danno.jpg"
    repo.registra(766, path, caricato_da=1, caricato_da_nome="Agenzia")
    repo.registra(766, path, caricato_da=2, caricato_da_nome="Avvocato")

    assert repo.caricato_da(path) == 2
    assert repo.path_caricati_da(766, 1) == set()
    assert repo.path_caricati_da(766, 2) == {str(path)}
