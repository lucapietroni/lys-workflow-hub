"""Test unitari per `save_upload` (upload foto/documenti dal portale esterno, v3.0 fase 6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lys_workflow_hub.core.pratica_files import UploadRifiutato, save_upload


def test_save_upload_foto_salva_in_cartella_foto(tmp_path: Path) -> None:
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="danno.jpg",
        raw=b"fake-jpeg-bytes",
    )
    assert target.exists()
    assert target.parent == tmp_path / "Pratiche" / "766" / "Pubblici" / "Foto"
    assert target.read_bytes() == b"fake-jpeg-bytes"
    assert target.name.startswith("danno_")
    assert target.suffix == ".jpg"


def test_save_upload_documento_salva_in_cartella_allegati(tmp_path: Path) -> None:
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="documento",
        filename="preventivo.pdf",
        raw=b"%PDF-1.4 fake",
    )
    assert target.parent == tmp_path / "Pratiche" / "766" / "Pubblici" / "Allegati"
    assert target.name.startswith("preventivo_")


def test_save_upload_foto_rifiuta_estensione_non_immagine(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="foto",
            filename="virus.exe",
            raw=b"MZ-fake",
        )


def test_save_upload_documento_rifiuta_estensione_pericolosa(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="documento",
            filename="script.exe",
            raw=b"MZ-fake",
        )


def test_save_upload_rifiuta_file_vuoto(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="foto",
            filename="vuoto.jpg",
            raw=b"",
        )


def test_save_upload_rifiuta_file_troppo_grande(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="foto",
            filename="grande.jpg",
            raw=b"x" * 100,
            max_bytes=50,
        )


def test_save_upload_sanifica_path_traversal(tmp_path: Path) -> None:
    """Un nome file con componenti di percorso non deve scrivere fuori dalla
    cartella della pratica — solo il basename viene usato."""
    target = save_upload(
        archivio_root=tmp_path,
        numero_pratica=766,
        categoria="foto",
        filename="../../etc/danno.jpg",
        raw=b"fake",
    )
    assert target.parent == tmp_path / "Pratiche" / "766" / "Pubblici" / "Foto"
    assert ".." not in target.parts


def test_save_upload_non_sovrascrive_file_omonimo(tmp_path: Path) -> None:
    """Due upload con lo stesso nome file originale non devono mai collidere
    (niente sovrascrittura silenziosa di foto/documenti già archiviati)."""
    t1 = save_upload(
        archivio_root=tmp_path, numero_pratica=766, categoria="foto",
        filename="danno.jpg", raw=b"primo",
    )
    t2 = save_upload(
        archivio_root=tmp_path, numero_pratica=766, categoria="foto",
        filename="danno.jpg", raw=b"secondo",
    )
    assert t1 != t2
    assert t1.read_bytes() == b"primo"
    assert t2.read_bytes() == b"secondo"


def test_save_upload_categoria_non_valida(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_upload(
            archivio_root=tmp_path,
            numero_pratica=766,
            categoria="qualcosa_altro",
            filename="file.pdf",
            raw=b"dati",
        )
