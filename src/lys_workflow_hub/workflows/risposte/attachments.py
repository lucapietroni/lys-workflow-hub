"""Suggeritore di allegati per le bozze di risposta (M4).

Usa lo scanner condiviso ``core.pratica_files.scan`` che legge le
cartelle WinCar ``Pratiche/<N>/Pubblici/{Foto,Allegati}/`` e classifica
i file in foto/denunce/cessioni/altri. Applica poi una regola per
categoria classificata da M3 per decidere quali file pre-spuntare.

Regola di default (modificabile via codice; in futuro potra' arrivare
da un YAML):

  * richiesta_documenti  -> pre-spunta foto + denunce + altri.
                            Tipico: la compagnia chiede integrazione
                            documentale; conviene mostrare tutto il
                            corredo (escluse cessioni: gia' inviate).
  * liquidazione         -> nessun allegato pre-spuntato (risposta
                            tipica: conferma o coordinate gia'
                            comunicate).
  * nomina_perito        -> nessun allegato (si concorda solo
                            l'appuntamento col perito).
  * altro                -> nessun allegato pre-spuntato.

Tutti i file della cartella vengono comunque ritornati nella lista,
con ``included=False`` quando la regola non li pre-spunta, cosi'
l'editor del cruscotto possa mostrarli e permettere il check manuale.

Cartelle inesistenti (es. pratica senza foto) vengono gestite
silenziosamente: lista vuota, nessuna eccezione.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from lys_workflow_hub.core.draft_repository import DraftAttachment
from lys_workflow_hub.core.mail_in_repository import (
    CAT_ALTRO,
    CAT_LIQUIDAZIONE,
    CAT_NOMINA_PERITO,
    CAT_PRESA_IN_CARICO,
    CAT_RICHIESTA_DOCUMENTI,
)
from lys_workflow_hub.core.pratica_files import (
    Allegato,
    AllegatiPratica,
    scan as scan_pratica,
)


logger = logging.getLogger(__name__)


# Etichetta visualizzata nell'editor per ogni Allegato in base alla
# categoria interna (foto / denuncia / cessione / altro).
_LABEL_PER_CATEGORIA = {
    "foto": "Foto",
    "denuncia": "Denuncia / Verbale",
    "cessione": "Cessione del credito",
    "altro": "Documento",
}


@dataclass(frozen=True)
class SuggestionResult:
    """Esito del suggerimento per una specifica pratica."""

    pratica_root: Path
    allegati: list[DraftAttachment]
    raw: AllegatiPratica

    @property
    def n_inclusi(self) -> int:
        return sum(1 for a in self.allegati if a.included)

    @property
    def n_totali(self) -> int:
        return len(self.allegati)


def _label_per(item: Allegato) -> str:
    prefisso = _LABEL_PER_CATEGORIA.get(item.categoria, "Documento")
    return f"{prefisso}: {item.nome_file}"


def _include_per_categoria(item: Allegato, categoria_m3: str) -> bool:
    if categoria_m3 == CAT_RICHIESTA_DOCUMENTI:
        return item.categoria in ("foto", "denuncia", "altro")
    if categoria_m3 == CAT_LIQUIDAZIONE:
        return False
    if categoria_m3 == CAT_NOMINA_PERITO:
        return False
    if categoria_m3 == CAT_PRESA_IN_CARICO:
        return False
    return False


def _to_draft_attachment(item: Allegato, *, included: bool) -> DraftAttachment:
    return DraftAttachment(
        path=str(item.path),
        label=_label_per(item),
        included=included,
    )


def suggerisci(
    *,
    archivio_root: Path,
    numero_pratica: int,
    categoria_m3: str,
) -> SuggestionResult:
    """Scansiona la pratica e produce la lista di DraftAttachment con
    pre-spunta applicata in base alla categoria M3.

    Idempotente, sicuro: cartelle inesistenti -> lista vuota.
    """
    raw = scan_pratica(Path(archivio_root), int(numero_pratica))

    draft_atts: list[DraftAttachment] = []
    for item in raw.tutti:
        draft_atts.append(
            _to_draft_attachment(
                item,
                included=_include_per_categoria(item, categoria_m3),
            )
        )

    return SuggestionResult(
        pratica_root=Path(archivio_root) / "Pratiche" / str(numero_pratica),
        allegati=draft_atts,
        raw=raw,
    )


def conta_inclusi(allegati: list[DraftAttachment]) -> int:
    """Conta gli allegati con included=True (per badge UI)."""
    return sum(1 for a in allegati if a.included)


__all__ = [
    "SuggestionResult",
    "suggerisci",
    "conta_inclusi",
]
