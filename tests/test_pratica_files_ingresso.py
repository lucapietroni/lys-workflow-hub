"""Test unitari per `save_ingresso_file` (staging documenti ingresso officina)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lys_workflow_hub.core.pratica_files import UploadRifiutato, cartella_ingresso, save_ingresso_file


def test_save_ingresso_file_foto_salva_in_staging(tmp_path: Path) -> None:
    target = save_ingresso_file(
        archivio_root=tmp_path,
        ingresso_id=42,
        categoria="foto",
        filename="danno.jpg",
        raw=b"fake-jpeg-bytes",
    )
    assert target.exists()
    assert target.parent == cartella_ingresso(tmp_path, 42)
    assert target.parent == tmp_path / "IngressiOfficina" / "42"
    assert target.read_bytes() == b"fake-jpeg-bytes"


def test_save_ingresso_file_documento_salva_in_staging(tmp_path: Path) -> None:
    target = save_ingresso_file(
        archivio_root=tmp_path,
        ingresso_id=42,
        categoria="documento",
        filename="cid.pdf",
        raw=b"%PDF-1.4 fake",
    )
    assert target.parent == tmp_path / "IngressiOfficina" / "42"
    assert target.name.startswith("cid_")


def test_save_ingresso_file_fuori_da_cartella_pratiche(tmp_path: Path) -> None:
    """Lo staging non deve mai finire sotto Pratiche/: quella cartella la
    scansiona anche WinCar, un ingresso non ancora collegato non deve
    comparire lì."""
    save_ingresso_file(
        archivio_root=tmp_path, ingresso_id=1, categoria="foto", filename="a.jpg", raw=b"x"
    )
    assert not (tmp_path / "Pratiche").exists()


def test_save_ingresso_file_rifiuta_estensione_non_immagine(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_ingresso_file(
            archivio_root=tmp_path, ingresso_id=1, categoria="foto", filename="virus.exe", raw=b"MZ"
        )


def test_save_ingresso_file_rifiuta_file_vuoto(tmp_path: Path) -> None:
    with pytest.raises(UploadRifiutato):
        save_ingresso_file(
            archivio_root=tmp_path, ingresso_id=1, categoria="documento", filename="cid.pdf", raw=b""
        )


def test_save_ingresso_file_categoria_non_valida_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_ingresso_file(
            archivio_root=tmp_path, ingresso_id=1, categoria="altro", filename="a.pdf", raw=b"x"
        )


def test_save_ingresso_file_nomi_duplicati_non_si_sovrascrivono(tmp_path: Path) -> None:
    t1 = save_ingresso_file(
        archivio_root=tmp_path, ingresso_id=1, categoria="documento", filename="cid.pdf", raw=b"uno"
    )
    t2 = save_ingresso_file(
        archivio_root=tmp_path, ingresso_id=1, categoria="documento", filename="cid.pdf", raw=b"due"
    )
    assert t1 != t2
    assert t1.read_bytes() == b"uno"
    assert t2.read_bytes() == b"due"
