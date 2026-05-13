"""Test del modulo di archiviazione delle cessioni firmate."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lys_workflow_hub.workflows.cessione_credito.archive import (
    filename_firmata,
    list_signed_pdfs,
    save_signed_pdf,
)


# Un PDF "minimo": header valido + EOF, sufficiente a passare la validazione.
MINI_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\nxref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 1 >>\nstartxref\n0\n%%EOF\n"


def test_filename_firmata_has_date_in_yyyymmdd():
    from datetime import date as _date
    name = filename_firmata(_date(2026, 5, 13))
    assert name == "Cessione_credito_20260513_firmata.pdf"


def test_save_creates_pratica_path(tmp_path: Path):
    result = save_signed_pdf(
        archivio_root=tmp_path,
        numero_pratica=766,
        pdf_bytes=MINI_PDF,
    )
    assert result.pratica_path.exists()
    assert result.pratica_path.parent == (
        tmp_path / "Pratiche" / "766" / "Pubblici" / "Allegati"
    )
    assert result.pratica_path.read_bytes() == MINI_PDF
    assert result.archivio_path is None
    assert result.backup_path is None


def test_save_creates_central_archive_when_configured(tmp_path: Path):
    central = tmp_path / "central"
    result = save_signed_pdf(
        archivio_root=tmp_path,
        numero_pratica=766,
        pdf_bytes=MINI_PDF,
        central_archive_root=central,
    )
    assert result.archivio_path is not None
    assert result.archivio_path.exists()
    assert result.archivio_path.read_bytes() == MINI_PDF


def test_save_backs_up_existing_file(tmp_path: Path):
    save_signed_pdf(archivio_root=tmp_path, numero_pratica=766, pdf_bytes=MINI_PDF)
    # secondo salvataggio: deve fare backup del primo
    time.sleep(1.1)  # garantisce timestamp diverso nel nome backup
    result = save_signed_pdf(
        archivio_root=tmp_path, numero_pratica=766, pdf_bytes=MINI_PDF + b"v2",
    )
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.backup_path.read_bytes() == MINI_PDF
    assert result.pratica_path.read_bytes() == MINI_PDF + b"v2"


def test_save_rejects_empty():
    with pytest.raises(ValueError):
        save_signed_pdf(archivio_root=Path("/tmp"), numero_pratica=1, pdf_bytes=b"")


def test_save_rejects_non_pdf(tmp_path: Path):
    with pytest.raises(ValueError):
        save_signed_pdf(
            archivio_root=tmp_path, numero_pratica=1, pdf_bytes=b"hello not a pdf",
        )


def test_list_signed_pdfs_empty(tmp_path: Path):
    assert list_signed_pdfs(tmp_path, 999) == []


def test_list_signed_pdfs_returns_archived(tmp_path: Path):
    save_signed_pdf(archivio_root=tmp_path, numero_pratica=12, pdf_bytes=MINI_PDF)
    listing = list_signed_pdfs(tmp_path, 12)
    assert len(listing) == 1
    assert listing[0].nome_file.endswith(".pdf")
    assert listing[0].dimensione_bytes == len(MINI_PDF)
    assert "KB" in listing[0].size_label or "MB" in listing[0].size_label
