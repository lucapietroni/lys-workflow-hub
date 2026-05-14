"""Workflow B — Richiesta risarcimento per atti vandalici (M2).

Espone le funzioni principali del workflow per essere importate dai router web.
"""
from lys_workflow_hub.workflows.risarcimento_vandalismo.allegati import (
    Allegato,
    AllegatiPratica,
    cartella_allegati,
    cartella_foto,
    filtra_per_nome,
    scan,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.data import (
    AUTORITA_DENUNCIA,
    CARROZZERIA_CAP,
    CARROZZERIA_COMUNE,
    CARROZZERIA_NOME,
    CARROZZERIA_PIVA,
    CARROZZERIA_PROVINCIA,
    CARROZZERIA_VIA,
    RichiestaVandalismoData,
    from_pratica,
)
from lys_workflow_hub.workflows.risarcimento_vandalismo.pec_generator import (
    build_all,
    build_body,
    build_subject,
    filename_bozza,
    selezione_nomi_default,
)


__all__ = [
    "AUTORITA_DENUNCIA",
    "Allegato",
    "AllegatiPratica",
    "CARROZZERIA_CAP",
    "CARROZZERIA_COMUNE",
    "CARROZZERIA_NOME",
    "CARROZZERIA_PIVA",
    "CARROZZERIA_PROVINCIA",
    "CARROZZERIA_VIA",
    "RichiestaVandalismoData",
    "build_all",
    "build_body",
    "build_subject",
    "cartella_allegati",
    "cartella_foto",
    "filename_bozza",
    "filtra_per_nome",
    "from_pratica",
    "scan",
    "selezione_nomi_default",
]
