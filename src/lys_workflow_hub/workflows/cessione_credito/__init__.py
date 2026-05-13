"""Workflow A — Cessione del credito.

Espone le funzioni principali del workflow per essere importate dai router web.
"""
from lys_workflow_hub.workflows.cessione_credito.archive import (
    CessioneFirmata,
    SaveResult,
    filename_firmata,
    list_signed_pdfs,
    save_signed_pdf,
)
from lys_workflow_hub.workflows.cessione_credito.data import (
    CARROZZERIA_NOME,
    CessioneData,
    from_pratica,
)
from lys_workflow_hub.workflows.cessione_credito.generator import (
    filename_for,
    generate,
)
from lys_workflow_hub.workflows.cessione_credito.pdf_converter import (
    PdfConversionError,
    docx_bytes_to_pdf_bytes,
)


__all__ = [
    "CARROZZERIA_NOME",
    "CessioneData",
    "CessioneFirmata",
    "PdfConversionError",
    "SaveResult",
    "docx_bytes_to_pdf_bytes",
    "filename_firmata",
    "filename_for",
    "from_pratica",
    "generate",
    "list_signed_pdfs",
    "save_signed_pdf",
]
