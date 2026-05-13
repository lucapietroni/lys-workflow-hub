"""Conversione .docx -> PDF.

Strategia: usiamo `docx2pdf` (Word COM su Windows). Se Word non e' installato
o COM va in errore, segnaliamo l'errore in modo esplicito cosi' l'utente puo'
o installare Word, o passare a un fallback LibreOffice (futuro M2).

`docx_bytes_to_pdf_bytes()` accetta i byte del .docx (cosi' come li produce il
generatore) e restituisce i byte del PDF. Tutto via filesystem temporaneo, mai
in memoria pura, perche' docx2pdf richiede percorsi fisici.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path


logger = logging.getLogger(__name__)


class PdfConversionError(RuntimeError):
    """Sollevata quando la conversione docx -> PDF fallisce."""


def docx_bytes_to_pdf_bytes(docx_bytes: bytes) -> bytes:
    """Converte i byte di un .docx in byte PDF.

    Usa `docx2pdf` (Microsoft Word via COM su Windows). Per servizi non-GUI
    futuri si potra' switchare a LibreOffice headless.
    """
    try:
        from docx2pdf import convert
    except ImportError as exc:  # pragma: no cover
        raise PdfConversionError(
            "Libreria docx2pdf non installata. Esegui: pip install docx2pdf"
        ) from exc

    with tempfile.TemporaryDirectory(prefix="lys_cessione_") as tmp:
        tmp_dir = Path(tmp)
        docx_path = tmp_dir / "cessione.docx"
        pdf_path = tmp_dir / "cessione.pdf"
        docx_path.write_bytes(docx_bytes)

        try:
            # docx2pdf accetta percorsi sia come Path che come stringa.
            # In modalita' single-file scrive il PDF accanto al docx.
            convert(str(docx_path), str(pdf_path))
        except Exception as exc:  # noqa: BLE001
            logger.exception("docx2pdf ha sollevato un'eccezione")
            raise PdfConversionError(
                "Conversione PDF fallita. Verifica che Microsoft Word sia "
                "installato e raggiungibile sul PC."
            ) from exc

        if not pdf_path.exists():
            raise PdfConversionError(
                "Conversione PDF fallita: nessun file prodotto. "
                "Word potrebbe essere occupato o non autorizzato a girare in background."
            )

        return pdf_path.read_bytes()
