"""Workflow: Verbali di consegna/riconsegna veicolo di cortesia."""
from lys_workflow_hub.workflows.verbale_cortesia.data import VerbaleData, from_pratica
from lys_workflow_hub.workflows.verbale_cortesia.generator import generate, filename_for
from lys_workflow_hub.workflows.verbale_cortesia.archive import save_verbale, list_verbali
from lys_workflow_hub.workflows.cessione_credito.pdf_converter import (
    PdfConversionError,
    docx_bytes_to_pdf_bytes,
)

__all__ = [
    "VerbaleData",
    "from_pratica",
    "generate",
    "filename_for",
    "save_verbale",
    "list_verbali",
    "PdfConversionError",
    "docx_bytes_to_pdf_bytes",
]
