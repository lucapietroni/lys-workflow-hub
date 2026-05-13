"""Workflow A — Cessione del credito.

Espone le funzioni principali del workflow per essere importate dai router web.
"""
from lys_workflow_hub.workflows.cessione_credito.data import (
    CARROZZERIA_NOME,
    CessioneData,
    from_pratica,
)
from lys_workflow_hub.workflows.cessione_credito.generator import (
    filename_for,
    generate,
)


__all__ = [
    "CARROZZERIA_NOME",
    "CessioneData",
    "from_pratica",
    "filename_for",
    "generate",
]
