"""Workflow C — Lettura risposte assicurazioni (M3).

Espone le funzioni principali del workflow per essere importate dai router web
e dallo script di polling.
"""
from lys_workflow_hub.workflows.risposte.matcher import (
    MatchResult,
    match_mail,
)


__all__ = [
    "MatchResult",
    "match_mail",
]
